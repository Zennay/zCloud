import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_runnable_priority_admission import (
    AdmissionError,
    rank_runnable_projects,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))


def evidence(*rows):
    return {"schema_version": 1, "projects": list(rows)}


class RunnablePriorityAdmissionTests(unittest.TestCase):
    def test_only_runnable_and_safety_admitted_projects_receive_priority(self):
        result = rank_runnable_projects(
            evidence(
                {"project_id": "lightup", "runnable": False, "safety_admitted": True},
                {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
                {"project_id": "haxlab", "runnable": True, "safety_admitted": True},
            ),
            contracts=CONTRACTS,
        )
        self.assertEqual(["ftmo", "haxlab"], [x["project_id"] for x in result["candidates"]])
        excluded = {x["project_id"]: x for x in result["excluded"]}
        self.assertFalse(excluded["lightup"]["priority_effective"])
        self.assertEqual(0, excluded["lightup"]["priority_weight"])

    def test_blocked_turbo_never_outranks_runnable_background(self):
        result = rank_runnable_projects(
            evidence(
                {"project_id": "lightup", "runnable": True, "safety_admitted": False},
                {"project_id": "supa", "runnable": True, "safety_admitted": True},
            ),
            contracts=CONTRACTS,
        )
        self.assertEqual(["supa"], [x["project_id"] for x in result["candidates"]])
        self.assertEqual("turbo", result["excluded"][0]["declared_priority"])
        self.assertEqual(0, result["excluded"][0]["priority_weight"])

    def test_priority_order_is_deterministic_inside_admitted_set(self):
        result = rank_runnable_projects(
            evidence(
                {"project_id": "haxlab", "runnable": True, "safety_admitted": True},
                {"project_id": "cloud", "runnable": True, "safety_admitted": True},
                {"project_id": "zguard", "runnable": True, "safety_admitted": True},
                {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
            ),
            contracts=CONTRACTS,
        )
        self.assertEqual(
            ["ftmo", "cloud", "zguard", "haxlab"],
            [x["project_id"] for x in result["candidates"]],
        )

    def test_priority_never_changes_runnable_or_safety_evidence(self):
        payload = evidence(
            {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
            {"project_id": "lightup", "runnable": False, "safety_admitted": True},
        )
        before = copy.deepcopy(payload)
        result = rank_runnable_projects(payload, contracts=CONTRACTS)
        self.assertEqual(before, payload)
        self.assertFalse(result["guardrails"]["priority_may_create_runnable_work"])
        self.assertFalse(result["guardrails"]["priority_may_bypass_blockers"])
        self.assertFalse(result["guardrails"]["priority_may_bypass_resource_guards"])

    def test_malformed_or_ambiguous_evidence_fails_closed(self):
        cases = [
            ({"schema_version": 2, "projects": []}, "evidence_schema_invalid"),
            ({"schema_version": True, "projects": []}, "evidence_schema_invalid"),
            ({"schema_version": 1.0, "projects": []}, "evidence_schema_invalid"),
            ({"schema_version": 1, "projects": {}}, "evidence_projects_missing"),
            (
                {"schema_version": 1, "projects": [], "prompt": "do not accept"},
                "evidence_unknown_top_level_field",
            ),
            (
                evidence({"project_id": "ftmo", "runnable": 1, "safety_admitted": True}),
                "runnable_invalid",
            ),
            (
                evidence({"project_id": "ftmo", "runnable": True, "safety_admitted": "yes"}),
                "safety_admitted_invalid",
            ),
            (
                evidence({"project_id": " ftmo", "runnable": True, "safety_admitted": True}),
                "project_id_invalid",
            ),
            (
                evidence({"project_id": "missing", "runnable": True, "safety_admitted": True}),
                "project_unknown",
            ),
            (
                evidence(
                    {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
                    {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
                ),
                "project_duplicate",
            ),
            (
                evidence(
                    {
                        "project_id": "ftmo",
                        "runnable": True,
                        "safety_admitted": True,
                        "task": "secret free text",
                    }
                ),
                "evidence_unknown_field",
            ),
        ]
        for payload, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(AdmissionError, error):
                    rank_runnable_projects(payload, contracts=CONTRACTS)

    def test_unknown_priority_contract_fails_closed(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["projects"]["ftmo"]["compute"]["priority"] = "urgent"
        with self.assertRaisesRegex(AdmissionError, "priority_invalid"):
            rank_runnable_projects(
                evidence({"project_id": "ftmo", "runnable": True, "safety_admitted": True}),
                contracts=contracts,
            )

    def test_cli_emits_bounded_structured_output(self):
        payload = evidence(
            {"project_id": "lightup", "runnable": False, "safety_admitted": True},
            {"project_id": "ftmo", "runnable": True, "safety_admitted": True},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "zcloud_runnable_priority_admission.py"),
                    "--evidence",
                    str(path),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, proc.returncode, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertTrue(output["ok"])
        self.assertEqual("ftmo", output["result"]["candidates"][0]["project_id"])
        rendered = json.dumps(output)
        self.assertNotIn("task", rendered)
        self.assertNotIn("prompt", rendered)
        self.assertNotIn("conversation", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
