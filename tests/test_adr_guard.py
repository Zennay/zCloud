import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_adr_guard",
    ROOT / "scripts" / "zcloud_adr_guard.py",
)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


VALID_ADR = """# ADR-0042: Test high-blast decision

## Status

Proposed

## Context

A cross-plane change needs durable rationale.

## Decision

Use the bounded test decision.

## Consequences

The change is explicit and reviewable.

## Evidence

Regression coverage proves the intended behavior.

## Rollback

Remove the candidate change and restore the previous implementation.
"""


class AdrGuardTests(unittest.TestCase):
    def test_low_blast_change_does_not_require_adr(self):
        result = guard.evaluate(["scripts/one_helper.py"])
        self.assertTrue(result["ok"])
        self.assertFalse(result["high_blast"])
        self.assertEqual([], result["adr_paths"])

    def test_service_plus_browser_requires_adr(self):
        with self.assertRaisesRegex(
            guard.AdrGuardError,
            "high-blast architecture change requires a versioned ADR",
        ):
            guard.evaluate([
                "server.py",
                "firefox-extension/background.js",
            ])

    def test_three_control_planes_require_adr(self):
        with self.assertRaises(guard.AdrGuardError):
            guard.evaluate([
                "server.py",
                "firefox-extension/background.js",
                "resource-policy.json",
            ])

    def test_six_production_files_require_adr(self):
        with self.assertRaises(guard.AdrGuardError):
            guard.evaluate([
                "scripts/a.py",
                "scripts/b.py",
                "scripts/c.py",
                "scripts/d.py",
                "scripts/e.py",
                "scripts/f.py",
            ])

    def test_test_and_docs_breadth_do_not_create_false_high_blast(self):
        paths = [
            f"tests/test_case_{index}.py"
            for index in range(10)
        ] + [
            "docs/notes/a.md",
            "docs/notes/b.md",
        ]
        result = guard.evaluate(paths)
        self.assertFalse(result["high_blast"])
        self.assertEqual([], result["production_paths"])

    def test_valid_versioned_adr_allows_high_blast_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adr = root / "docs" / "adr" / "0042-test-decision.md"
            adr.parent.mkdir(parents=True)
            adr.write_text(VALID_ADR, encoding="utf-8")
            result = guard.evaluate(
                [
                    "server.py",
                    "firefox-extension/background.js",
                    "docs/adr/0042-test-decision.md",
                ],
                root,
            )
        self.assertTrue(result["high_blast"])
        self.assertEqual(
            ["docs/adr/0042-test-decision.md"],
            result["adr_paths"],
        )
        self.assertEqual("Proposed", result["validated_adrs"][0]["status"])

    def test_malformed_adr_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adr = root / "docs" / "adr" / "0042-test-decision.md"
            adr.parent.mkdir(parents=True)
            adr.write_text(
                VALID_ADR.replace(
                    "## Rollback\n\nRemove the candidate change and restore the previous implementation.\n",
                    "",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(guard.AdrGuardError, "missing required heading"):
                guard.evaluate(
                    [
                        "server.py",
                        "firefox-extension/background.js",
                        "docs/adr/0042-test-decision.md",
                    ],
                    root,
                )

    def test_repo_bootstrap_adr_is_valid(self):
        result = guard.validate_adr(
            ROOT / "docs" / "adr" / "0001-high-blast-architecture-decisions.md",
            "docs/adr/0001-high-blast-architecture-decisions.md",
        )
        self.assertEqual(1, result["number"])
        self.assertEqual("Proposed", result["status"])

    def test_guard_reuses_transactional_promoter_blast_semantics(self):
        paths = [
            "server.py",
            "firefox-extension/background.js",
            "projects.json",
        ]
        selected = guard.production_paths(paths)
        self.assertEqual(
            guard.promotion_blast_radius(selected),
            guard.evaluate(
                paths + ["docs/adr/0001-high-blast-architecture-decisions.md"]
            )["blast_radius"],
        )


class AdrGuardWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_hosted_and_read_only(self):
        text = (
            ROOT / ".github" / "workflows" / "zcloud-high-blast-adr.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}",
            text,
        )
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn(
            '--base-sha "$BASE_SHA" --head-sha "$HEAD_SHA"',
            text,
        )
        for forbidden in ("sudo", "systemctl", "/home/ubuntu", "contents: write"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
