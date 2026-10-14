import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_idle_capacity_borrow_admission import (
    BorrowAdmissionError,
    admit_idle_capacity_borrow,
)

ROOT = Path(__file__).resolve().parents[1]


def payload(*projects, pressure_state="healthy", backpressure_required=False):
    return {
        "schema_version": 1,
        "host": {
            "capacity_cpu_cores": 6,
            "protected_reserve_cpu_cores": 1.5,
            "admitted_non_protected_cpu_cores": 2,
            "pressure_state": pressure_state,
            "backpressure_required": backpressure_required,
        },
        "projects": list(projects),
    }


def project(
    project_id,
    rank,
    requested,
    *,
    runnable=True,
    safety_admitted=True,
    allow=True,
    protected=False,
):
    return {
        "project_id": project_id,
        "scheduler_rank": rank,
        "runnable": runnable,
        "safety_admitted": safety_admitted,
        "allow_idle_capacity_borrow": allow,
        "protected": protected,
        "requested_extra_cpu_cores": requested,
    }


class IdleCapacityBorrowAdmissionTests(unittest.TestCase):
    def test_borrows_only_idle_capacity_in_explicit_scheduler_order(self):
        evidence = payload(
            project("ftmo", 0, 2.0),
            project("haxlab", 1, 1.0),
        )
        before = copy.deepcopy(evidence)
        result = admit_idle_capacity_borrow(evidence)
        self.assertEqual(before, evidence)
        self.assertEqual(2.5, result["host"]["idle_borrowable_cpu_cores"])
        self.assertEqual(["ftmo", "haxlab"], [x["project_id"] for x in result["admitted"]])
        self.assertEqual(2.0, result["admitted"][0]["borrow_cpu_cores"])
        self.assertEqual(0.5, result["admitted"][1]["borrow_cpu_cores"])
        self.assertFalse(result["admitted"][1]["fully_satisfied"])
        self.assertEqual(0.0, result["host"]["remaining_idle_cpu_cores"])
        self.assertEqual(2.5, result["totals"]["borrowed_cpu_cores"])

    def test_never_spends_protected_reserve_or_preempts_existing_allocation(self):
        result = admit_idle_capacity_borrow(payload(project("ftmo", 0, 99.0)))
        self.assertEqual(2.5, result["totals"]["borrowed_cpu_cores"])
        self.assertEqual(
            6.0,
            result["host"]["protected_reserve_cpu_cores"]
            + result["host"]["admitted_non_protected_cpu_cores"]
            + result["totals"]["borrowed_cpu_cores"],
        )
        self.assertFalse(
            result["guardrails"]["protected_reserve_spendable_by_borrowers"]
        )
        self.assertFalse(
            result["guardrails"]["borrowing_may_preempt_existing_allocation"]
        )

    def test_runnable_safety_and_project_intent_are_all_required(self):
        result = admit_idle_capacity_borrow(
            payload(
                project("not-runnable", 0, 1, runnable=False),
                project("unsafe", 1, 1, safety_admitted=False),
                project("disabled", 2, 1, allow=False),
                project("control", 3, 1, protected=True),
                project("zero", 4, 0),
            )
        )
        reasons = {x["project_id"]: x["reason"] for x in result["denied"]}
        self.assertEqual("not_runnable", reasons["not-runnable"])
        self.assertEqual("safety_not_admitted", reasons["unsafe"])
        self.assertEqual("borrowing_not_allowed", reasons["disabled"])
        self.assertEqual("protected_project_not_borrower", reasons["control"])
        self.assertEqual("no_extra_capacity_requested", reasons["zero"])
        self.assertEqual([], result["admitted"])

    def test_memory_io_or_explicit_backpressure_blocks_borrowing(self):
        for pressure in ("memory_pressure", "io_pressure", "mixed_pressure"):
            with self.subTest(pressure=pressure):
                result = admit_idle_capacity_borrow(
                    payload(project("ftmo", 0, 1), pressure_state=pressure)
                )
                self.assertEqual([], result["admitted"])
                self.assertEqual("backpressure_active", result["denied"][0]["reason"])
        result = admit_idle_capacity_borrow(
            payload(project("ftmo", 0, 1), backpressure_required=True)
        )
        self.assertEqual("backpressure_active", result["denied"][0]["reason"])

    def test_compute_busy_alone_does_not_block_idle_borrowing(self):
        result = admit_idle_capacity_borrow(
            payload(project("ftmo", 0, 1), pressure_state="compute_busy")
        )
        self.assertEqual(1.0, result["totals"]["borrowed_cpu_cores"])
        self.assertFalse(result["guardrails"]["compute_busy_alone_blocks_borrowing"])

    def test_no_idle_capacity_denies_without_touching_reserve(self):
        evidence = payload(project("ftmo", 0, 1))
        evidence["host"]["admitted_non_protected_cpu_cores"] = 4.5
        result = admit_idle_capacity_borrow(evidence)
        self.assertEqual(0.0, result["host"]["idle_borrowable_cpu_cores"])
        self.assertEqual("idle_capacity_exhausted", result["denied"][0]["reason"])

    def test_ambiguous_or_malformed_evidence_fails_closed(self):
        cases = []

        item = payload()
        item["schema_version"] = True
        cases.append((item, "evidence_schema_invalid"))

        item = payload()
        item["host"]["protected_reserve_cpu_cores"] = 7
        cases.append((item, "protected_reserve_exceeds_capacity"))

        item = payload()
        item["host"]["admitted_non_protected_cpu_cores"] = 5
        cases.append((item, "admitted_non_protected_exceeds_ceiling"))

        item = payload()
        item["host"]["pressure_state"] = "unknown"
        cases.append((item, "pressure_state_invalid"))

        item = payload(project("ftmo", 0, 1), project("haxlab", 0, 1))
        cases.append((item, "scheduler_rank_duplicate"))

        item = payload(project(" ftmo", 0, 1))
        cases.append((item, "project_id_invalid"))

        item = payload(project("ftmo", 0, 1))
        item["projects"][0]["prompt"] = "free text is not accepted"
        cases.append((item, "project_fields_invalid"))

        item = payload(project("ftmo", 0, 1))
        item["host"]["extra"] = 1
        cases.append((item, "host_fields_invalid"))

        for evidence, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(BorrowAdmissionError, error):
                    admit_idle_capacity_borrow(evidence)

    def test_cli_output_is_structured_and_bounded(self):
        evidence = payload(
            project("ftmo", 0, 1),
            project("haxlab", 1, 1, runnable=False),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "zcloud_idle_capacity_borrow_admission.py"),
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
        self.assertEqual(1.0, output["result"]["totals"]["borrowed_cpu_cores"])
        rendered = json.dumps(output)
        self.assertNotIn("prompt", rendered)
        self.assertNotIn("conversation", rendered)
        self.assertNotIn("task", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
