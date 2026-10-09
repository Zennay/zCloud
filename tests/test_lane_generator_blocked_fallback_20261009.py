"""Pure regressions for rejected queued fallback admission in zCloud lanes.

Test fixture only; no live queue, database, VPS, browser or network access.
The lane's public blockers describe the *selected* task or an entirely
blocked lane, not stale conflicts from a higher-priority rejected candidate.
"""
import unittest

from lane_generator import eligible_queue_lane_map, generate_execution_lanes

PROJECT = {"id": "cloud", "lane_profile": "platform"}


def task(queue_id, title, priority, files, status="queued"):
    return {
        "queue_id": queue_id,
        "project_id": "cloud",
        "title": title,
        "completion_criteria": "",
        "priority": priority,
        "status": status,
        "created_at": "2026-10-09T12:00:00+00:00",
        "metadata": {"conflict_scope": {"files": files}},
    }


def lane(backlog, name="runtime-automation", claims=()):
    return next(
        row for row in generate_execution_lanes(PROJECT, backlog, claims)
        if row["lane_id"] == name
    )


class RejectedCandidateFallbackTests(unittest.TestCase):
    def test_rejected_high_priority_does_not_taint_admitted_lower_priority(self):
        active = task("control", "Implement queue scheduler", "P1",
                      ["shared.py"], status="running")
        rejected = task("conflicting", "Fix Firefox browser", "P0",
                        ["shared.py"])
        accepted = task("safe", "Repair Firefox tab state", "P2",
                        ["browser/other.py"])
        backlog = [accepted, rejected, active]
        runtime = lane(backlog)
        self.assertEqual("queued", runtime["status"])
        self.assertEqual("safe", runtime["queue_id"])
        self.assertEqual([], runtime["blocked_by"])
        self.assertEqual(["safe"], list(eligible_queue_lane_map(
            PROJECT, backlog
        )))

    def test_all_rejected_candidates_preserve_explanatory_blockers(self):
        active = task("control", "Implement queue scheduler", "P1",
                      ["shared.py"], status="running")
        rejected_p0 = task("candidate-p0", "Repair Firefox automation", "P0",
                           ["shared.py"])
        rejected_p1 = task("candidate-p1", "Fix browser runtime", "P1",
                           ["shared.py"])
        runtime = lane([rejected_p1, active, rejected_p0])
        self.assertEqual("blocked", runtime["status"])
        self.assertIsNone(runtime["queue_id"])
        self.assertEqual(2, len(runtime["blocked_by"]))
        self.assertTrue(all(x["queue_id"] == "control"
                            and x["reason"] == "queue_scope_conflict"
                            for x in runtime["blocked_by"]))

    def test_claim_rejection_must_not_taint_later_safe_candidate(self):
        claim = [{
            "project_id": "cloud",
            "claim_key": "browser-write",
            "owner_id": "peer-worker",
            "metadata": {"conflict_scope": {"files": ["browser/input.py"]}},
        }]
        rejected = task("conflicting", "Fix Firefox automation", "P0",
                        ["browser/input.py"])
        accepted = task("safe", "Fix Firefox tab state", "P1",
                        ["browser/safe.py"])
        runtime = lane([accepted, rejected], claims=claim)
        self.assertEqual("safe", runtime["queue_id"])
        self.assertEqual("queued", runtime["status"])
        self.assertEqual([], runtime["blocked_by"])

    def test_claim_rejection_without_alternative_remains_blocked(self):
        claim = [{
            "project_id": "cloud",
            "claim_key": "browser-write",
            "owner_id": "peer-worker",
            "metadata": {"conflict_scope": {"files": ["browser/input.py"]}},
        }]
        rejected = task("conflicting", "Fix Firefox automation", "P0",
                        ["browser/input.py"])
        runtime = lane([rejected], claims=claim)
        self.assertEqual("blocked", runtime["status"])
        self.assertIsNone(runtime["queue_id"])
        self.assertEqual("task_claim_scope_conflict",
                         runtime["blocked_by"][0]["reason"])

    def test_other_lane_blockers_stay_visible_after_fallback(self):
        active = task("control", "Implement queue scheduler", "P1",
                      ["shared.py"], status="running")
        rejected_runtime = task("blocked-browser", "Repair Firefox automation",
                                "P0", ["shared.py"])
        accepted_runtime = task("safe-browser", "Fix Firefox tabs", "P2",
                                ["browser/tab.py"])
        rejected_deploy = task("blocked-deploy", "Repair VPS deploy workflow",
                               "P1", ["shared.py"])
        rows = generate_execution_lanes(
            PROJECT, [accepted_runtime, rejected_deploy,
                      active, rejected_runtime]
        )
        by_id = {row["lane_id"]: row for row in rows}
        self.assertEqual("safe-browser",
                         by_id["runtime-automation"]["queue_id"])
        self.assertEqual([], by_id["runtime-automation"]["blocked_by"])
        self.assertEqual("blocked", by_id["deploy-ops"]["status"])
        self.assertTrue(by_id["deploy-ops"]["blocked_by"])


if __name__ == "__main__":
    unittest.main()
