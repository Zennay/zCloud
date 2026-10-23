"""Hosted-only trust contract for the exact-slot regression workflow.

Source checks do not touch the VPS, queue, Firefox profile, runner or GitHub.
"""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-force-exact-slot-hosted.yml")
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def safety_errors(source: str) -> tuple[str, ...]:
    errors = []
    if "  pull_request:" not in source:
        errors.append("not_pr_triggered")
    if "    runs-on: ubuntu-latest" not in source:
        errors.append("not_hosted")
    if "    runs-on: [self-hosted" in source or "    runs-on: self-hosted" in source:
        errors.append("untrusted_self_hosted")
    if CHECKOUT not in source or "actions/checkout@v4" in source:
        errors.append("floating_checkout")
    if "persist-credentials: false" not in source:
        errors.append("checkout_credentials")
    if "ref: ${{ github.event.pull_request.head.sha }}" not in source:
        errors.append("unbound_source_ref")
    if "EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}" not in source:
        errors.append("unbound_expected_sha")
    if 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' not in source:
        errors.append("no_exact_sha_validation")
    if "python3 -m unittest -v tests.test_force_exact_slot_hosted_trust_20261014" not in source:
        errors.append("no_executable_self_regression")
    if re.search(r"(?m)^permissions:\s*\n\s*contents: write", source):
        errors.append("write_permissions")
    return tuple(errors)


class ForceExactSlotHostedTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_workflow_meets_static_policy(self):
        self.assertEqual((), safety_errors(self.source))

    def test_hosted_only_and_no_vps_resource_access(self):
        self.assertIn("runs-on: ubuntu-latest", self.source)
        self.assertNotIn("runs-on: [self-hosted", self.source)
        self.assertNotIn("runs-on: self-hosted", self.source)

    def test_immutable_head_checkout_and_token_not_persisted(self):
        self.assertIn(CHECKOUT, self.source)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.source)
        self.assertIn("persist-credentials: false", self.source)

    def test_candidate_sha_is_checked_before_tests(self):
        marker = "- name: Enforce exact candidate SHA and repo provenance"
        regression = "- name: Check syntax and screenshot regression end to end"
        self.assertLess(self.source.index(marker), self.source.index(regression))
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.source)
        self.assertIn('test "$GITHUB_EVENT_NAME" = pull_request', self.source)
        self.assertIn('test "$BASE_REPO" = Zennay/zCloud', self.source)

    def test_floating_checkout_regression_is_detected(self):
        source = self.source.replace(CHECKOUT, "actions/checkout@v4")
        self.assertIn("floating_checkout", safety_errors(source))

    def test_missing_ref_regression_is_detected(self):
        source = self.source.replace("ref: ${{ github.event.pull_request.head.sha }}",
                                     "ref: main")
        self.assertIn("unbound_source_ref", safety_errors(source))

    def test_missing_exact_sha_validation_is_detected(self):
        source = self.source.replace(
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            'echo "source maybe current"')
        self.assertIn("no_exact_sha_validation", safety_errors(source))

    def test_accidental_self_hosted_runner_is_detected(self):
        source = self.source.replace("runs-on: ubuntu-latest",
                                     "runs-on: [self-hosted, zcloud, vps]")
        self.assertIn("untrusted_self_hosted", safety_errors(source))

    def test_token_persistence_guard_is_detected(self):
        source = self.source.replace("persist-credentials: false",
                                     "persist-credentials: true")
        self.assertIn("checkout_credentials", safety_errors(source))

    def test_write_permission_is_detected(self):
        source = self.source.replace("  contents: read", "  contents: write")
        self.assertIn("write_permissions", safety_errors(source))

    def test_regression_still_executes_all_existing_real_checks(self):
        for command in (
            "python3 -m py_compile server.py project_runtime.py lane_generator.py",
            "node --check public/app.js",
            "node --check firefox-extension/worker-controller-v2.js",
            "node --check public/zcloud-worker-v2-engine.js",
            "node --check public/zcloud-worker.user.js",
            "tests.test_global_worker_console_ui tests.test_portfolio_queue_backend",
            "node tests/test_single_tab_worker_v2.js",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.source)

    def test_self_regression_is_detected_if_removed(self):
        source = self.source.replace(
            "python3 -m unittest -v tests.test_force_exact_slot_hosted_trust_20261014",
            'echo "skipped"')
        self.assertIn("no_executable_self_regression", safety_errors(source))


if __name__ == "__main__":
    unittest.main()
