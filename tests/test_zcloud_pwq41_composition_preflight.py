#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-pwq41-composition-preflight.yml"
SPEC = importlib.util.spec_from_file_location(
    "zcloud_pwq41_composition_preflight",
    ROOT / "scripts" / "zcloud_pwq41_composition_preflight.py",
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class Pwq41CompositionPreflightTests(unittest.TestCase):
    def write_requirement_files(self, root: Path) -> None:
        grouped: dict[str, list[str]] = {}
        for requirement in preflight.REQUIREMENTS:
            grouped.setdefault(requirement["path"], []).extend(requirement["tokens"])
        for relative, tokens in grouped.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(tokens) + "\n", encoding="utf-8")

    def test_complete_contract_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_requirement_files(root)
            result = preflight.audit(root)
        self.assertTrue(result["ready"], result)
        self.assertTrue(all(item["present"] for item in result["checks"]))

    def test_missing_semantic_is_reported_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_requirement_files(root)
            target = root / "server.py"
            target.write_text(
                target.read_text(encoding="utf-8").replace(
                    "RUNNER_COMMAND_LONG_STALE_SECONDS", "REMOVED_LONG_STALE"
                ),
                encoding="utf-8",
            )
            before = target.read_bytes()
            result = preflight.audit(root)
            after = target.read_bytes()
        self.assertFalse(result["ready"])
        self.assertEqual(before, after)
        server_check = next(
            item for item in result["checks"]
            if item["id"] == "server_long_replacement_lease"
        )
        self.assertIn("RUNNER_COMMAND_LONG_STALE_SECONDS", server_check["missing_tokens"])

    def test_shared_overlap_paths_are_explicitly_not_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_requirement_files(root)
            result = preflight.audit(root)
        self.assertEqual(
            [
                ".github/workflows/push-all-workers-now.yml",
                "tests/test_push_all_workers_workflow.py",
            ],
            result["shared_paths_not_claimed"],
        )

    def test_expected_head_mismatch_fails_require_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_requirement_files(root)
            code = preflight.main(
                [
                    "--root",
                    str(root),
                    "--expected-head",
                    "deadbeef",
                    "--require-ready",
                    "--json",
                ]
            )
        self.assertEqual(2, code)

    def test_workflow_is_exact_head_read_only_and_permanent_vps_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn('--expected-head "$EXPECTED_SHA"', text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertNotIn("contents: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("systemctl restart", text)


if __name__ == "__main__":
    unittest.main()
