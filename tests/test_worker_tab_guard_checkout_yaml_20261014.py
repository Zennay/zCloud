"""Regression: each worker-tab workflow checkout has one unambiguous mapping.

Duplicate `with:` YAML keys can either invalidate a GitHub Actions workflow
or cause the first block's immutable `ref` and `clean` values to be discarded.
This test is hosted-only and never touches the VPS or any browser session.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-worker-tab-guard.yml")
CHECKOUT_HEADER = re.compile(r"(?m)^      - uses: actions/checkout@[^\n]+$")
NEXT_STEP = re.compile(r"(?m)^      - (?:uses|name):")


def checkout_blocks(source: str) -> list[str]:
    blocks = []
    for hit in CHECKOUT_HEADER.finditer(source):
        tail = source[hit.end():]
        following = NEXT_STEP.search(tail)
        blocks.append(tail[:following.start()] if following else tail)
    return blocks


def invalid_checkout_reasons(source: str) -> list[str]:
    errors = []
    blocks = checkout_blocks(source)
    if len(blocks) != 2:
        errors.append("unexpected_checkout_count")
    for i, block in enumerate(blocks):
        if len(re.findall(r"(?m)^        with:\s*$", block)) != 1:
            errors.append(f"checkout_{i}_ambiguous_with")
        if not re.search(r"(?m)^          ref: \$\{\{ github\.sha \}\}\s*$", block):
            errors.append(f"checkout_{i}_missing_exact_ref")
        if not re.search(r"(?m)^          persist-credentials: false\s*$", block):
            errors.append(f"checkout_{i}_credentials_not_disabled")
        if not re.search(r"(?m)^          clean: true\s*$", block):
            errors.append(f"checkout_{i}_unclean_workspace")
    return errors


class WorkerTabCheckoutStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_both_checkouts_have_unambiguous_with_mapping(self):
        self.assertEqual([], invalid_checkout_reasons(self.source))

    def test_exactly_two_checkout_steps(self):
        self.assertEqual(2, len(checkout_blocks(self.source)))

    def test_duplicate_key_fixture_is_rejected(self):
        bad = self.source.replace(
            "          clean: true\n",
            "          clean: true\n        with:\n          persist-credentials: false\n",
            1,
        )
        self.assertIn("checkout_0_ambiguous_with", invalid_checkout_reasons(bad))

    def test_missing_immutable_ref_is_rejected(self):
        bad = self.source.replace("          ref: ${{ github.sha }}\n", "", 1)
        self.assertIn("checkout_0_missing_exact_ref", invalid_checkout_reasons(bad))

    def test_persisted_credentials_are_rejected(self):
        bad = self.source.replace(
            "          persist-credentials: false\n",
            "          persist-credentials: true\n",
            1,
        )
        self.assertIn("checkout_0_credentials_not_disabled", invalid_checkout_reasons(bad))

    def test_unclean_checkout_is_rejected(self):
        bad = self.source.replace("          clean: true\n", "          clean: false\n", 1)
        self.assertIn("checkout_0_unclean_workspace", invalid_checkout_reasons(bad))

    def test_live_install_remains_owner_manual_main_only(self):
        install = self.source.split("\n  install:\n", 1)[1]
        gate = install.split("    runs-on:", 1)[0]
        for required in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.apply == true",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(required=required):
                self.assertIn(required, gate)


if __name__ == "__main__":
    unittest.main()
