"""Prevent old branch-only cockpit CI from silently skipping new PR changes.

Tests run entirely on hosted GitHub runners and do not inspect VPS state.
"""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-worker-cockpit-ui-hosted.yml")
PINNED = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
HEAD_EXPR = "${{ github.event.pull_request.head.sha || github.sha }}"


def missing_trust_requirements(source: str) -> list[str]:
    requirements = {
        "pr": "  pull_request:",
        "main_push": "      - main",
        "hosted": "runs-on: ubuntu-latest",
        "pinned": PINNED,
        "immutable_source": "ref: " + HEAD_EXPR,
        "immutable_expected": "EXPECTED_SHA: " + HEAD_EXPR,
        "exact_check": 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "credentials": "persist-credentials: false",
        "repo": 'test "$REPOSITORY" = Zennay/zCloud',
        "selftest": "tests.test_worker_cockpit_ui_hosted_trust_20261014",
        "existing_js": "node tests/test_worker_debug_panel.js",
    }
    errors = [name for name, fragment in requirements.items() if fragment not in source]
    if "actions/checkout@v4" in source:
        errors.append("floating_checkout")
    if "runs-on: self-hosted" in source or "runs-on: [self-hosted" in source:
        errors.append("privileged_runner")
    if "contents: write" in source or "actions: write" in source:
        errors.append("write_permission")
    return errors


class WorkerCockpitUiHostedTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_original_workflow_is_accepted(self):
        self.assertEqual([], missing_trust_requirements(self.source))

    def test_pr_main_and_existing_branch_triggers_preserved(self):
        for fragment in ("  pull_request:", "  push:", "  workflow_dispatch:",
                         "      - fix/worker-allocation-cockpit-20261013",
                         "      - main"):
            self.assertIn(fragment, self.source)

    def test_checkout_is_pinned_and_credential_free(self):
        self.assertIn(PINNED, self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertIn("clean: true", self.source)
        self.assertIn("fetch-depth: 1", self.source)

    def test_exact_sha_precedes_any_ui_test(self):
        guard = self.source.index("Confirm tested candidate SHA")
        js = self.source.index("Check JavaScript syntax and workflow trust")
        self.assertLess(guard, js)
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.source)

    def test_all_old_ui_contract_names_preserved(self):
        for name in (
            "test_dashboard_ux_hierarchy.py",
            "test_global_worker_console_ui.py",
            "test_mobile_worker_actions_20261010.py",
            "test_mobile_worker_console_navigation.py",
            "test_mobile_css_delivery_20261010.py",
            "test_dashboard_design_system.py",
            "test_dashboard_start_force_regression.py",
            "node tests/test_worker_debug_panel.js",
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.source)

    def test_unknown_pr_revision_is_denied(self):
        changed = self.source.replace("ref: " + HEAD_EXPR, "ref: main")
        self.assertIn("immutable_source", missing_trust_requirements(changed))

    def test_unbound_expected_sha_is_denied(self):
        changed = self.source.replace("EXPECTED_SHA: " + HEAD_EXPR,
                                      "EXPECTED_SHA: latest")
        self.assertIn("immutable_expected", missing_trust_requirements(changed))

    def test_floating_checkout_is_denied(self):
        changed = self.source.replace(PINNED, "actions/checkout@v4")
        problems = missing_trust_requirements(changed)
        self.assertIn("pinned", problems)
        self.assertIn("floating_checkout", problems)

    def test_self_hosted_pr_execution_is_denied(self):
        changed = self.source.replace("runs-on: ubuntu-latest",
                                      "runs-on: [self-hosted, zcloud, vps]")
        self.assertIn("privileged_runner", missing_trust_requirements(changed))

    def test_write_permission_is_denied(self):
        changed = self.source.replace("contents: read", "contents: write")
        self.assertIn("write_permission", missing_trust_requirements(changed))

    def test_missing_pull_request_trigger_is_denied(self):
        changed = self.source.replace("  pull_request:", "  # pull_request disabled:")
        self.assertIn("pr", missing_trust_requirements(changed))

    def test_credential_persistence_is_denied(self):
        changed = self.source.replace("persist-credentials: false",
                                      "persist-credentials: true")
        self.assertIn("credentials", missing_trust_requirements(changed))


if __name__ == "__main__":
    unittest.main()
