from __future__ import annotations

import unittest

from scripts.zcloud_resource_scheduler_composition import (
    CompositionError,
    compose_scheduler_plan,
)


def base_payload() -> dict:
    return {
        "schema_version": 1,
        "host": {
            "capacity_cpu_cores": 6,
            "protected_reserve_cpu_cores": 1,
            "idle_borrowable_cpu_cores": 3,
            "backpressure_required": False,
            "queue_pressure_state": "healthy",
            "source_complete": True,
        },
        "projects": [
            {
                "project_id": "cloud",
                "scheduler_rank": 0,
                "runnable": True,
                "safety_admitted": True,
                "allow_idle_capacity_borrow": False,
                "protected": True,
                "minimum_cpu_cores": 1,
                "target_cpu_cores": 1,
                "maximum_cpu_cores": 1,
                "requested_cpu_cores": 1,
                "safe_parallel_jobs": 0,
                "current_parallel_jobs": 1,
                "project_parallel_cap": 1,
                "cpu_per_job_cores": 1,
            },
            {
                "project_id": "ftmo",
                "scheduler_rank": 1,
                "runnable": True,
                "safety_admitted": True,
                "allow_idle_capacity_borrow": True,
                "protected": False,
                "minimum_cpu_cores": 0,
                "target_cpu_cores": 3,
                "maximum_cpu_cores": 5,
                "requested_cpu_cores": 3,
                "safe_parallel_jobs": 2,
                "current_parallel_jobs": 1,
                "project_parallel_cap": 4,
                "cpu_per_job_cores": 1,
            },
            {
                "project_id": "haxlab",
                "scheduler_rank": 2,
                "runnable": True,
                "safety_admitted": True,
                "allow_idle_capacity_borrow": True,
                "protected": False,
                "minimum_cpu_cores": 0,
                "target_cpu_cores": 1,
                "maximum_cpu_cores": 2,
                "requested_cpu_cores": 1,
                "safe_parallel_jobs": 2,
                "current_parallel_jobs": 1,
                "project_parallel_cap": 3,
                "cpu_per_job_cores": 0.5,
            },
        ],
    }


class ResourceSchedulerCompositionTests(unittest.TestCase):
    def test_composes_base_demand_then_borrows_only_remaining_cpu(self):
        result = compose_scheduler_plan(base_payload())

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["decision_ready"])
        self.assertEqual(
            [(row["project_id"], row["cpu_cores"]) for row in result["base_allocations"]],
            [("cloud", 1.0), ("ftmo", 3.0), ("haxlab", 1.0)],
        )
        self.assertEqual(
            result["borrow_admissions"],
            [{
                "project_id": "ftmo",
                "scheduler_rank": 1,
                "additional_jobs": 1,
                "additional_cpu_cores": 1.0,
            }],
        )
        self.assertEqual(result["totals"]["planned_cpu_cores"], 6.0)
        self.assertEqual(result["totals"]["borrowed_cpu_cores"], 1.0)
        self.assertEqual(result["totals"]["admitted_parallel_jobs"], 1)
        self.assertFalse(result["runtime_mutation"])
        self.assertFalse(result["guardrails"]["mutation_performed"])

    def test_scheduler_order_applies_when_normal_demand_exceeds_remaining_capacity(self):
        payload = base_payload()
        payload["host"]["capacity_cpu_cores"] = 4
        payload["host"]["idle_borrowable_cpu_cores"] = 0

        result = compose_scheduler_plan(payload)

        allocations = {row["project_id"]: row for row in result["base_allocations"]}
        self.assertEqual(allocations["cloud"]["cpu_cores"], 1.0)
        self.assertEqual(allocations["ftmo"]["cpu_cores"], 3.0)
        self.assertEqual(allocations["haxlab"]["cpu_cores"], 0.0)
        self.assertFalse(allocations["haxlab"]["target_satisfied"])
        self.assertEqual(result["totals"]["planned_cpu_cores"], 4.0)

    def test_fractional_job_cost_never_creates_partial_job_or_oversubscription(self):
        payload = base_payload()
        payload["host"]["capacity_cpu_cores"] = 5.75
        payload["host"]["idle_borrowable_cpu_cores"] = 0.75
        payload["projects"][1]["cpu_per_job_cores"] = 0.5
        payload["projects"][1]["safe_parallel_jobs"] = 4

        result = compose_scheduler_plan(payload)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["totals"]["planned_cpu_cores"], 5.5)
        self.assertEqual(result["totals"]["borrowed_cpu_cores"], 0.5)
        self.assertEqual(result["totals"]["admitted_parallel_jobs"], 1)
        self.assertLessEqual(
            result["totals"]["planned_cpu_cores"],
            payload["host"]["capacity_cpu_cores"],
        )

    def test_backpressure_holds_borrowing_but_keeps_safe_base_plan(self):
        payload = base_payload()
        payload["host"]["backpressure_required"] = True

        result = compose_scheduler_plan(payload)

        self.assertEqual(result["status"], "held")
        self.assertTrue(result["decision_ready"])
        self.assertEqual(result["reason"], "backpressure_required")
        self.assertEqual(result["borrow_admissions"], [])
        self.assertEqual(result["totals"]["planned_cpu_cores"], 5.0)
        self.assertEqual(result["totals"]["borrowed_cpu_cores"], 0.0)

    def test_busy_actions_queue_holds_expansion(self):
        payload = base_payload()
        payload["host"]["queue_pressure_state"] = "busy"

        result = compose_scheduler_plan(payload)

        self.assertEqual(result["status"], "held")
        self.assertTrue(result["decision_ready"])
        self.assertEqual(result["reason"], "queue_pressure_busy")
        self.assertEqual(result["borrow_admissions"], [])

    def test_incomplete_source_fails_closed(self):
        payload = base_payload()
        payload["host"]["source_complete"] = False

        result = compose_scheduler_plan(payload)

        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["decision_ready"])
        self.assertEqual(result["reason"], "source_incomplete")
        self.assertEqual(result["borrow_admissions"], [])
        self.assertFalse(result["runtime_mutation"])

    def test_non_runnable_project_keeps_only_its_minimum(self):
        payload = base_payload()
        payload["projects"][1]["runnable"] = False

        result = compose_scheduler_plan(payload)

        rows = {row["project_id"]: row for row in result["base_allocations"]}
        self.assertEqual(rows["ftmo"]["cpu_cores"], 0.0)
        self.assertNotIn("ftmo", {row["project_id"] for row in result["borrow_admissions"]})

    def test_rejects_protected_minimum_larger_than_reserved_capacity(self):
        payload = base_payload()
        payload["projects"][0]["minimum_cpu_cores"] = 2
        payload["projects"][0]["target_cpu_cores"] = 2
        payload["projects"][0]["maximum_cpu_cores"] = 2
        payload["projects"][0]["requested_cpu_cores"] = 2

        with self.assertRaisesRegex(CompositionError, "protected_minimum_exceeds_reserved_capacity"):
            compose_scheduler_plan(payload)

    def test_rejects_duplicate_scheduler_rank(self):
        payload = base_payload()
        payload["projects"][2]["scheduler_rank"] = 1

        with self.assertRaisesRegex(CompositionError, "duplicate_scheduler_rank"):
            compose_scheduler_plan(payload)

    def test_rejects_boolean_numeric_fields(self):
        payload = base_payload()
        payload["projects"][1]["requested_cpu_cores"] = True

        with self.assertRaisesRegex(CompositionError, "ftmo.requested_cpu_cores_invalid"):
            compose_scheduler_plan(payload)

    def test_rejects_request_above_profile_maximum(self):
        payload = base_payload()
        payload["projects"][1]["requested_cpu_cores"] = 6

        with self.assertRaisesRegex(CompositionError, "ftmo.requested_exceeds_maximum"):
            compose_scheduler_plan(payload)

    def test_minimum_capacity_overflow_returns_bounded_blocked_plan(self):
        payload = base_payload()
        payload["host"]["capacity_cpu_cores"] = 1
        payload["host"]["protected_reserve_cpu_cores"] = 1
        payload["host"]["idle_borrowable_cpu_cores"] = 0
        payload["projects"][1]["minimum_cpu_cores"] = 1
        payload["projects"][1]["target_cpu_cores"] = 1
        payload["projects"][1]["requested_cpu_cores"] = 1

        result = compose_scheduler_plan(payload)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["decision_ready"])
        self.assertEqual(result["reason"], "minimum_capacity_exceeded")
        self.assertEqual(result["base_allocations"], [])
        self.assertEqual(result["borrow_admissions"], [])
        self.assertFalse(result["runtime_mutation"])


if __name__ == "__main__":
    unittest.main()
