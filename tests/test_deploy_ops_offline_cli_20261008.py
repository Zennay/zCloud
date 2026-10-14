"""Black-box CLI contracts for the offline deploy admission guard."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ops_offline_admission_20261008.py"
SHA_MAIN = "a" * 40
SHA_HEAD = "b" * 40

def sample():
    return {
        "canonical_repository": "Zennay/zCloud",
        "main_sha": SHA_MAIN,
        "candidate_base_sha": SHA_MAIN,
        "candidate_head_sha": SHA_HEAD,
        "serialized_gates": {"580": "released", "1089": "released"},
        "ownership_inventory_complete": True,
        "owner_overlap": False,
        "dashboard_recovery_pr_mutation": False,
        "checks": {
            name: {"status": "success", "head_sha": SHA_HEAD, "main_sha": SHA_MAIN}
            for name in ("regression", "permanent_vps", "production_receipt", "dashboard_external")
        },
    }

def cli(raw):
    with tempfile.TemporaryDirectory() as folder:
        evidence = Path(folder) / "evidence.json"
        evidence.write_text(raw, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(evidence)],
            text=True, capture_output=True, check=False, timeout=10,
        )
    return result, json.loads(result.stdout)

class CliContracts(unittest.TestCase):
    def test_consistent_evidence_still_never_authorizes_release(self):
        result, verdict = cli(json.dumps(sample()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(verdict["admissible"])
        self.assertIs(verdict["release_authorized"], False)
        self.assertIs(verdict["mutation_performed"], False)

    def test_invalid_json_is_safe_and_machine_readable(self):
        result, verdict = cli("{ malformed")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(verdict["reason"], "unreadable_evidence")
        self.assertIs(verdict["release_authorized"], False)
        self.assertIs(verdict["mutation_performed"], False)

    def test_real_world_closed_gate_is_rejected(self):
        evidence = sample()
        evidence["serialized_gates"]["580"] = "active"
        result, verdict = cli(json.dumps(evidence))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(verdict["reason"], "serialized_gate_closed_or_unknown")
        self.assertIs(verdict["release_authorized"], False)

    def test_unsafe_dashboard_recovery_is_rejected(self):
        evidence = sample()
        evidence["dashboard_recovery_pr_mutation"] = True
        result, verdict = cli(json.dumps(evidence))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(verdict["reason"], "unsafe_pr_recovery")

if __name__ == "__main__":
    unittest.main()
