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
        self.assertEqual(1, guard.compare(old, new)["new_findings"])

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

    def test_hosted_no_new_debt_gate_runs_for_all_workflow_and_action_edits(self):
        workflow = (ROOT / ".github/workflows" /
                    "zcloud-container-digest-ratchet-20261015-w2.yml").read_text()
        self.assertIn('".github/workflows/**"', workflow)
        self.assertIn('".github/actions/**"', workflow)
        self.assertIn('"**/action.yml"', workflow)
        self.assertIn('"**/action.yaml"', workflow)
        self.assertIn('"action.yml"', workflow)
        self.assertIn('"action.yaml"', workflow)
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

    def test_invalid_sha_fails_closed(self):
        check = subprocess.run(
            [sys.executable, str(SCRIPT), "--base-sha", "main", "--head-sha", "main"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(2, check.returncode)
        data = json.loads(check.stdout)
        self.assertFalse(data["release_authorized"])
        self.assertFalse(data["inventory_complete"])


if __name__ == "__main__":
    unittest.main()
