from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_adr_composition_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_adr_composition_audit.py"
CONTRACT = ROOT / "ops" / "adr_guard_composition_requirements.json"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-adr-composition-audit.yml"


def write_owner_fixture(root: Path, ref: str) -> None:
    for relative, markers in audit.SOURCE_MARKERS[ref].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(markers) + "\n", encoding="utf-8")


class AdrCompositionAuditTests(unittest.TestCase):
    def test_repository_contract_is_exact_and_bounded(self):
        payload = audit.load_contract(CONTRACT)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(audit.CAPABILITY, payload["capability"])
        self.assertEqual({"#589", "#709"}, {row["ref"] for row in payload["owners"]})
        self.assertEqual(audit.EXPECTED_CONTROLS, set(payload["required_controls"]))
        self.assertEqual(audit.EXPECTED_RESOLUTION, payload["resolution"])

    def test_complete_sources_preserve_both_unique_control_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = base / "pr589"
            newer = base / "pr709"
            old.mkdir()
            newer.mkdir()
            write_owner_fixture(old, "#589")
            write_owner_fixture(newer, "#709")
            result = audit.audit(
                CONTRACT,
                source_root_589=old,
                source_root_709=newer,
            )

        self.assertEqual("COMPOSITION_REQUIRED", result["decision"])
        self.assertEqual(["#589", "#709"], result["owner_refs"])
        self.assertTrue(result["sources_verified"])
        self.assertGreaterEqual(result["source_marker_count"], 10)
        self.assertEqual(11, result["required_control_count"])
        self.assertTrue(result["retire_only_after_composite_green"])
        self.assertFalse(result["mutation_performed"])

    def test_missing_unique_contract_control_fails_closed(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        payload["owners"][0]["unique_controls"].remove(
            "transactional_blast_classifier_parity"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                audit.CompositionAuditError,
                "contract_owner_control_drift",
            ):
                audit.load_contract(path)

    def test_missing_source_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = base / "pr589"
            newer = base / "pr709"
            old.mkdir()
            newer.mkdir()
            write_owner_fixture(old, "#589")
            write_owner_fixture(newer, "#709")
            guard = old / "scripts" / "zcloud_adr_guard.py"
            guard.write_text(
                guard.read_text(encoding="utf-8").replace(
                    "promotion_blast_radius",
                    "removed_marker",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                audit.CompositionAuditError,
                "source_marker_missing_589",
            ):
                audit.audit(
                    CONTRACT,
                    source_root_589=old,
                    source_root_709=newer,
                )

    def test_contract_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real.json"
            target.write_bytes(CONTRACT.read_bytes())
            link = root / "contract.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unsupported")
            with self.assertRaisesRegex(
                audit.CompositionAuditError,
                "symlink_rejected",
            ):
                audit.load_contract(link)

    def test_cli_strict_mode_requires_source_evidence(self):
        run = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--contract",
                str(CONTRACT),
                "--require-complete",
                "--json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, run.returncode, run.stderr)
        payload = json.loads(run.stdout)
        self.assertFalse(payload["sources_verified"])
        self.assertFalse(payload["mutation_performed"])

    def test_workflow_is_exact_head_read_only_and_permanent_vps_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("refs/pull/589/head", text)
        self.assertIn("refs/pull/709/head", text)
        self.assertIn("--require-complete", text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "sudo ",
            "systemctl ",
            "git push",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
