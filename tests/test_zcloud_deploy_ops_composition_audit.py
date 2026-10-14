import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_deploy_ops_composition_audit.py"
CONTRACT = ROOT / "ops" / "deploy_ops_composition_requirements.json"


class DeployOpsCompositionAuditTests(unittest.TestCase):
    def test_contract_is_valid_and_contains_exact_partial_overlap_pairs(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(3, payload["pair_count"])
        got = {
            (pair["workflow"], pair["newer_owner"], pair["older_owner"])
            for pair in payload["pairs"]
        }
        self.assertEqual(
            {
                (".github/workflows/recover-pending-workers.yml", 876, 689),
                (".github/workflows/prove-workers-generating-now.yml", 879, 691),
                (".github/workflows/reactivate-workers-now.yml", 878, 692),
            },
            got,
        )

    def test_generation_pair_preserves_read_only_and_privacy_obligations(self):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        pair = next(
            item
            for item in data["pairs"]
            if item["workflow"] == ".github/workflows/prove-workers-generating-now.yml"
        )
        older = "\n".join(pair["required_older_controls"])
        for required in (
            "mode=ro",
            "query_only=ON",
            "size/mtime stability",
            "bounded summaries",
            "exception type only",
        ):
            self.assertIn(required, older)

    def test_reactivation_pair_preserves_failure_log_redaction(self):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        pair = next(
            item
            for item in data["pairs"]
            if item["workflow"] == ".github/workflows/reactivate-workers-now.yml"
        )
        older = "\n".join(pair["required_older_controls"])
        self.assertIn("full service journals", older)
        self.assertIn("full process argv", older)
        self.assertIn("bounded systemd state", older)
        self.assertIn("complete settings/allocation/control payloads", older)

    def test_recovery_pair_preserves_runner_control_redaction(self):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        pair = next(
            item
            for item in data["pairs"]
            if item["workflow"] == ".github/workflows/recover-pending-workers.yml"
        )
        older = "\n".join(pair["required_older_controls"])
        self.assertIn("full target/project payloads", older)
        self.assertIn("raw runner-control responses", older)
        self.assertIn("raw exception repr", older)
        self.assertIn("command IDs and ok state", older)

    def test_invalid_duplicate_owner_fails_closed(self):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        data["pairs"][1]["older_owner"] = data["pairs"][0]["older_owner"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--contract", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("appears in multiple composition pairs", proc.stderr)

    def test_unknown_workflow_query_fails_closed(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--workflow", ".github/workflows/not-owned.yml"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("not registered exactly once", proc.stderr)


if __name__ == "__main__":
    unittest.main()
