import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_safe_parallel_expansion import (
    ParallelExpansionError,
    plan_parallel_expansion,
)

ROOT = Path(__file__).resolve().parents[1]


def row(
    project_id,
    rank,
    safe_jobs,
    current,
    cap,
    cpu,
    *,
    runnable=True,
    safety=True,
):
    return {
        "project_id": project_id,
        "scheduler_rank": rank,
        "runnable": runnable,
        "safety_admitted": safety,
        "safe_parallel_jobs": safe_jobs,
        "current_parallel_jobs": current,
        "project_parallel_cap": cap,
        "cpu_per_job_cores": cpu,
    }


def payload(*rows, idle=4.0, backpressure=False):
    return {
        "schema_version": 1,
        "host": {
            "idle_borrowable_cpu_cores": idle,
            "backpressure_required": backpressure,
        },
        "projects": list(rows),
    }


class SafeParallelExpansionTests(unittest.TestCase):
    def test_fills_safe_jobs_in_explicit_scheduler_order(self):
        evidence = payload(
            row("ftmo", 0, 2, 1, 4, 1.5),
            row("haxlab", 1, 3, 1, 4, 0.5),
        )
        before = copy.deepcopy(evidence)
        result = plan_parallel_expansion(evidence)
        self.assertEqual(before, evidence)
        self.assertEqual(["ftmo", "haxlab"], [x["project_id"] for x in result["admissions"]])
        self.assertEqual(2, result["admissions"][0]["admitted_jobs"])
        self.assertEqual(2, result["admissions"][1]["admitted_jobs"])
        self.assertEqual(4, result["totals"]["admitted_jobs"])
        self.assertEqual(4.0, result["totals"]["admitted_cpu_cores"])
        self.assertEqual(0.0, result["host"]["remaining_idle_borrowable_cpu_cores"])

    def test_never_exceeds_project_cap(self):
        result = plan_parallel_expansion(
            payload(row("ftmo", 0, 9, 3, 4, 0.5), idle=5)
        )
        self.assertEqual(1, result["admissions"][0]["admitted_jobs"])
        self.assertEqual(0, result["admissions"][0]["remaining_project_cap"])

    def test_only_whole_jobs_are_admitted(self):
        result = plan_parallel_expansion(
            payload(row("ftmo", 0, 3, 0, 4, 1.5), idle=2.0)
        )
        self.assertEqual(1, result["admissions"][0]["admitted_jobs"])
        self.assertEqual(1.5, result["totals"]["admitted_cpu_cores"])
        self.assertEqual(0.5, result["host"]["remaining_idle_borrowable_cpu_cores"])
        self.assertFalse(result["guardrails"]["partial_jobs_allowed"])

    def test_backpressure_blocks_all_new_jobs(self):
        result = plan_parallel_expansion(
            payload(row("ftmo", 0, 2, 0, 4, 1), backpressure=True)
        )
        self.assertEqual([], result["admissions"])
        self.assertEqual("backpressure_active", result["excluded"][0]["reason"])

    def test_runnable_and_safety_are_required(self):
        result = plan_parallel_expansion(
            payload(
                row("idle", 0, 1, 0, 2, 1, runnable=False),
                row("unsafe", 1, 1, 0, 2, 1, safety=False),
            )
        )
        reasons = {x["project_id"]: x["reason"] for x in result["excluded"]}
        self.assertEqual("not_runnable", reasons["idle"])
        self.assertEqual("safety_not_admitted", reasons["unsafe"])

    def test_zero_safe_jobs_and_full_cap_are_not_expanded(self):
        result = plan_parallel_expansion(
            payload(
                row("none", 0, 0, 0, 2, 1),
                row("full", 1, 3, 2, 2, 1),
            )
        )
        reasons = {x["project_id"]: x["reason"] for x in result["excluded"]}
        self.assertEqual("no_safe_parallel_jobs", reasons["none"])
        self.assertEqual("project_parallel_cap_reached", reasons["full"])

    def test_insufficient_cpu_does_not_admit_fractional_job(self):
        result = plan_parallel_expansion(
            payload(row("ftmo", 0, 2, 0, 4, 1.25), idle=1.0)
        )
        self.assertEqual([], result["admissions"])
        self.assertEqual(
            "insufficient_idle_cpu_for_one_job",
            result["excluded"][0]["reason"],
        )

    def test_malformed_evidence_fails_closed(self):
        cases = []

        item = payload()
        item["schema_version"] = 1.0
        cases.append((item, "schema_version_invalid"))

        item = payload(row("ftmo", 0, 1, 0, 2, 1))
        item["projects"][0]["prompt"] = "not allowed"
        cases.append((item, "project_fields_invalid"))

        item = payload(row("ftmo", 0, 1, 3, 2, 1))
        cases.append((item, "current_parallel_jobs_exceeds_cap"))

        item = payload(row("ftmo", 0, 65, 0, 100, 0.1))
        cases.append((item, "safe_parallel_jobs_limit"))

        item = payload(
            row("ftmo", 0, 1, 0, 2, 1),
            row("haxlab", 0, 1, 0, 2, 1),
        )
        cases.append((item, "scheduler_rank_duplicate"))

        item = payload(row(" ftmo", 0, 1, 0, 2, 1))
        cases.append((item, "project_id_invalid"))

        for evidence, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ParallelExpansionError, error):
                    plan_parallel_expansion(evidence)

    def test_cli_is_bounded_and_structured(self):
        evidence = payload(row("ftmo", 0, 1, 0, 2, 1), idle=1.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "zcloud_safe_parallel_expansion.py"),
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
        self.assertEqual(1, output["result"]["totals"]["admitted_jobs"])
        rendered = json.dumps(output)
        self.assertNotIn("prompt", rendered)
        self.assertNotIn("conversation", rendered)
        self.assertNotIn("task", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
