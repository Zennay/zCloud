"""Regression: every active queue task owns its scope, not only lane representative.

No services, database, browser or live queue are accessed. This exercises the
pure production lane planner with synthetic queue records.
"""
import unittest

from lane_generator import generate_execution_lanes, eligible_queue_lane_map


PROJECT = {"id": "cloud", "name": "zCloud", "lane_profile": "platform"}


def task(queue_id, title, status, files=(), priority="P1"):
    return {
        "queue_id": queue_id,
        "project_id": "cloud",
        "title": title,
        "completion_criteria": "",
        "status": status,
        "priority": priority,
        "created_at": "2026-10-01T12:00:00+00:00",
        "metadata": {"conflict_scope": {"files": list(files), "capabilities": []}},
    }


def lane_by_id(backlog, lane_id):
    return next(lane for lane in generate_execution_lanes(PROJECT, backlog)
                if lane["lane_id"] == lane_id)


class ActiveLaneScopeReservationTests(unittest.TestCase):
    def test_second_active_writer_blocks_cross_lane_queued_file(self):
        first = task("a", "Implement queue scheduler", "running", ["control/a.py"])
        second = task("b", "Fix queue claim", "verifying", ["shared/state.py"])
        waiting = task("c", "Repair Firefox automation", "queued",
                       ["shared/state.py"], priority="P0")
        lane = lane_by_id([waiting, second, first], "runtime-automation")
        self.assertIsNone(lane["queue_id"])
        self.assertEqual("blocked", lane["status"])
        self.assertEqual("b", lane["blocked_by"][0]["queue_id"])
        self.assertEqual(["shared/state.py"],
                         lane["blocked_by"][0]["overlap"]["files"])

    def test_third_active_writer_also_reserves_its_scope(self):
        active = [
            task("a", "Implement queue scheduler", "claimed", ["a"]),
            task("b", "Fix queue claim", "running", ["b"]),
            task("c", "Repair worker allocation", "verifying", ["nested/c.py"]),
        ]
        candidate = task("d", "Deploy VPS workflow", "queued", ["nested"],
                         priority="P0")
        lane = lane_by_id(active + [candidate], "deploy-ops")
        self.assertIsNone(lane["queue_id"])
        self.assertEqual("blocked", lane["status"])
        self.assertEqual("c", lane["blocked_by"][0]["queue_id"])

    def test_nonconflicting_other_lane_still_admitted(self):
        active = [
            task("a", "Implement queue scheduler", "running", ["a.py"]),
            task("b", "Fix queue claim", "verifying", ["shared.py"]),
        ]
        candidate = task("c", "Repair Firefox automation", "queued",
                         ["browser.js"])
        admitted = eligible_queue_lane_map(PROJECT, active + [candidate])
        self.assertIn("c", admitted)
        self.assertEqual("runtime-automation", admitted["c"]["lane_id"])

    def test_active_representative_and_result_count_remain_stable(self):
        first = task("a", "Implement queue scheduler", "running")
        second = task("b", "Fix queue claim", "verifying")
        lanes = generate_execution_lanes(PROJECT, [second, first])
        self.assertEqual(3, len(lanes))
        control = next(l for l in lanes if l["lane_id"] == "control-plane")
        # Stable created_at and queue_id tie-breakers still choose a.
        self.assertEqual("a", control["queue_id"])
        self.assertEqual("running", control["status"])
        self.assertEqual([], list(eligible_queue_lane_map(PROJECT, [second, first])))

    def test_unclaimed_lane_remains_unblocked_when_no_conflict(self):
        active = [task("a", "Implement queue scheduler", "running", ["a.py"]),
                  task("b", "Fix queue claim", "running", ["b.py"])]
        lane = lane_by_id(active, "deploy-ops")
        self.assertEqual("idle", lane["status"])
        self.assertEqual([], lane["blocked_by"])
    def test_scalar_file_on_active_queue_reserves_entire_path(self):
        active = task("a", "Implement queue scheduler", "running")
        active["metadata"]["conflict_scope"]["files"] = "shared/control.py"
        candidate = task("b", "Repair Firefox automation", "queued",
                         ["shared/control.py"])
        lane = lane_by_id([active, candidate], "runtime-automation")
        self.assertEqual("blocked", lane["status"])
        self.assertEqual("a", lane["blocked_by"][0]["queue_id"])
        self.assertEqual(["shared/control.py"],
                         lane["blocked_by"][0]["overlap"]["files"])

    def test_scalar_file_on_candidate_is_not_split_into_characters(self):
        active = task("a", "Implement queue scheduler", "running",
                      ["shared/control.py"])
        candidate = task("b", "Repair Firefox automation", "queued")
        candidate["metadata"]["conflict_scope"]["files"] = "shared/control.py"
        lane = lane_by_id([active, candidate], "runtime-automation")
        self.assertEqual("blocked", lane["status"])
        self.assertEqual(["shared/control.py"],
                         lane["blocked_by"][0]["overlap"]["files"])

    def test_scalar_capability_on_claim_blocks_matching_lane(self):
        candidate = task("a", "Repair Firefox automation", "queued")
        claims = [{
            "project_id": "cloud",
            "claim_key": "existing",
            "owner_id": "other",
            "metadata": {
                "conflict_scope": {"capabilities": "cloud:runtime-automation"}
            },
        }]
        lanes = generate_execution_lanes(PROJECT, [candidate], claims)
        lane = next(l for l in lanes if l["lane_id"] == "runtime-automation")
        self.assertEqual("blocked", lane["status"])
        self.assertEqual("claim", lane["blocked_by"][0]["kind"])
        self.assertEqual(["cloud:runtime-automation"],
                         lane["blocked_by"][0]["overlap"]["capabilities"])

    def test_scalar_capability_on_active_queue_blocks_other_lane(self):
        active = task("a", "Implement queue scheduler", "running")
        active["metadata"]["conflict_scope"]["capabilities"] = "shared-owner"
        candidate = task("b", "Deploy VPS workflow", "queued")
        candidate["metadata"]["conflict_scope"]["capabilities"] = [
            "shared-owner"
        ]
        lane = lane_by_id([active, candidate], "deploy-ops")
        self.assertEqual("blocked", lane["status"])
        self.assertEqual(["shared-owner"],
                         lane["blocked_by"][0]["overlap"]["capabilities"])



if __name__ == "__main__":
    unittest.main()
