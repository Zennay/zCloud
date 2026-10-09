import unittest

from lane_generator import (
    classify_backlog_item,
    eligible_queue_lane_map,
    generate_execution_lanes,
    scopes_overlap,
)


class LaneGeneratorTests(unittest.TestCase):
    def project(self, project_id="ftmo", profile="research-validation"):
        return {
            "id": project_id,
            "name": project_id,
            "lane_profile": profile,
        }

    def item(self, queue_id, title, priority="P1", status="queued", metadata=None):
        return {
            "queue_id": queue_id,
            "project_id": "ftmo",
            "title": title,
            "completion_criteria": "Implement the change with deterministic tests.",
            "priority": priority,
            "status": status,
            "created_at": "2026-10-01T00:00:00+00:00",
            "metadata": metadata or {},
        }

    def test_ftmo_backlog_derives_expected_three_lanes(self):
        backlog = [
            self.item("ftmo-critical", "Implement next generation candidate"),
            self.item("ftmo-qa", "Run frozen walk-forward validation"),
            self.item("ftmo-data", "Repair provider data provenance"),
        ]

        lanes = generate_execution_lanes(self.project(), backlog)
        selected = {lane["lane_id"]: lane["queue_id"] for lane in lanes}

        self.assertEqual("ftmo-critical", selected["critical-path"])
        self.assertEqual("ftmo-qa", selected["qa-validation"])
        self.assertEqual("ftmo-data", selected["data-provenance"])

    def test_same_lane_backlog_only_admits_one_queue_item(self):
        first = self.item("ftmo-a", "Implement next generation candidate", "P1")
        second = self.item("ftmo-b", "Implement another strategy candidate", "P2")
        lane_map = eligible_queue_lane_map(self.project(), [second, first])

        self.assertEqual(["ftmo-a"], list(lane_map))
        self.assertEqual("critical-path", lane_map["ftmo-a"]["lane_id"])

    def test_active_queue_item_occupies_lane_before_queued_duplicate(self):
        running = self.item(
            "ftmo-running",
            "Implement current generation candidate",
            "P2",
            status="running",
        )
        urgent = self.item(
            "ftmo-queued",
            "Implement urgent strategy recovery",
            "P0",
        )

        lanes = generate_execution_lanes(self.project(), [urgent, running])
        critical = next(lane for lane in lanes if lane["lane_id"] == "critical-path")

        self.assertEqual("ftmo-running", critical["queue_id"])
        self.assertEqual("running", critical["status"])
        self.assertNotIn("ftmo-queued", eligible_queue_lane_map(self.project(), [urgent, running]))

    def test_active_claim_blocks_overlapping_generated_lane_scope(self):
        backlog = [self.item("ftmo-data", "Repair provider data provenance", "P0")]
        claims = [{
            "project_id": "ftmo",
            "claim_key": "data:provider-repair",
            "owner_id": "other-worker",
            "worker_id": "ftmo::w9",
            "metadata": {
                "conflict_scope": {
                    "capabilities": ["ftmo-data-provenance"],
                    "files": [],
                }
            },
        }]

        lanes = generate_execution_lanes(self.project(), backlog, claims)
        data_lane = next(lane for lane in lanes if lane["lane_id"] == "data-provenance")

        self.assertIsNone(data_lane["queue_id"])
        self.assertEqual("blocked", data_lane["status"])
        self.assertEqual("data:provider-repair", data_lane["blocked_by"][0]["claim_key"])

    def test_explicit_file_scope_is_merged_and_checked(self):
        item = self.item(
            "ftmo-explicit",
            "Implement next generation candidate",
            metadata={
                "conflict_scope": {
                    "capabilities": [],
                    "files": ["research/generation.py"],
                }
            },
        )
        lane = classify_backlog_item(self.project(), item)

        overlap = scopes_overlap(
            lane["scope"],
            {
                "capabilities": [],
                "files": ["research"],
            },
        )

        self.assertEqual(["research"], overlap["files"])

    def test_cross_lane_explicit_file_collision_admits_only_higher_priority_writer(self):
        project = self.project("ftmo", "research-validation")
        critical = self.item(
            "ftmo-critical-file",
            "Implement next generation candidate",
            "P1",
            metadata={
                "conflict_scope": {
                    "capabilities": [],
                    "files": ["research/shared.py"],
                }
            },
        )
        validation = self.item(
            "ftmo-validation-file",
            "Run frozen walk-forward validation",
            "P0",
            metadata={
                "conflict_scope": {
                    "capabilities": [],
                    "files": ["research/shared.py"],
                }
            },
        )

        lanes = generate_execution_lanes(project, [critical, validation])
        selected = {lane["queue_id"] for lane in lanes if lane["queue_id"]}

        self.assertIn("ftmo-validation-file", selected)
        self.assertNotIn("ftmo-critical-file", selected)
        critical_lane = next(lane for lane in lanes if lane["lane_id"] == "critical-path")
        self.assertEqual("blocked", critical_lane["status"])
        self.assertEqual("queue_scope_conflict", critical_lane["blocked_by"][0]["reason"])
        self.assertEqual(["research/shared.py"], critical_lane["blocked_by"][0]["overlap"]["files"])

    def test_keyword_matching_does_not_treat_ui_as_substring_of_build(self):
        project = self.project("supa", "product")
        item = {
            "queue_id": "supa-build",
            "project_id": "supa",
            "title": "Build package",
            "completion_criteria": "Compile package deterministically.",
            "priority": "P1",
            "status": "queued",
            "created_at": "2026-10-01T00:00:00+00:00",
            "metadata": {},
        }

        lane = classify_backlog_item(project, item)

        self.assertEqual("quality-validation", lane["lane_id"])

    def test_cross_lane_explicit_file_collision_admits_only_higher_priority_scope(self):
        project = self.project("cloud", "platform")
        shared_scope = {
            "conflict_scope": {
                "capabilities": [],
                "files": ["server.py"],
            }
        }
        backlog = [
            {
                **self.item("cloud-control", "Implement queue scheduler lane allocation", "P1", metadata=shared_scope),
                "project_id": "cloud",
            },
            {
                **self.item("cloud-deploy", "Harden VPS deploy workflow", "P2", metadata=shared_scope),
                "project_id": "cloud",
            },
        ]

        lanes = generate_execution_lanes(project, backlog)
        control = next(lane for lane in lanes if lane["lane_id"] == "control-plane")
        deploy = next(lane for lane in lanes if lane["lane_id"] == "deploy-ops")

        self.assertEqual("cloud-control", control["queue_id"])
        self.assertIsNone(deploy["queue_id"])
        self.assertEqual("blocked", deploy["status"])
        self.assertEqual("queue", deploy["blocked_by"][0]["kind"])
        self.assertEqual("cloud-control", deploy["blocked_by"][0]["queue_id"])
        self.assertEqual(["server.py"], deploy["blocked_by"][0]["overlap"]["files"])

    def test_platform_lane_scopes_are_pairwise_non_overlapping(self):
        project = self.project("cloud", "platform")
        backlog = [
            {
                **self.item("cloud-control", "Implement queue scheduler lane allocation"),
                "project_id": "cloud",
            },
            {
                **self.item("cloud-runtime", "Fix Firefox userscript automation"),
                "project_id": "cloud",
            },
            {
                **self.item("cloud-deploy", "Harden VPS deploy workflow"),
                "project_id": "cloud",
            },
        ]

        lanes = [lane for lane in generate_execution_lanes(project, backlog) if lane["queue_id"]]
        self.assertEqual(3, len(lanes))
        for index, left in enumerate(lanes):
            for right in lanes[index + 1:]:
                overlap = scopes_overlap(left["scope"], right["scope"])
                self.assertEqual([], overlap["capabilities"])
                self.assertEqual([], overlap["files"])


    def test_security_lab_contract_derives_authorization_runtime_and_evidence_lanes(self):
        project = self.project("lightup", "security-lab")
        backlog = [
            {**self.item("lightup-scope", "Harden authorization scope guardrail"), "project_id": "lightup"},
            {**self.item("lightup-runtime", "Build passive assessment orchestration runtime"), "project_id": "lightup"},
            {**self.item("lightup-evidence", "Implement findings remediation retest evidence"), "project_id": "lightup"},
        ]

        lanes = generate_execution_lanes(project, backlog)
        selected = {lane["lane_id"]: lane["queue_id"] for lane in lanes}

        self.assertEqual("lightup-scope", selected["scope-authorization"])
        self.assertEqual("lightup-runtime", selected["assessment-runtime"])
        self.assertEqual("lightup-evidence", selected["evidence-remediation"])

    def test_unknown_explicit_lane_profile_fails_closed(self):
        project = self.project("lightup", "unknown-profile")
        item = {**self.item("lightup-unknown", "Implement assessment runtime"), "project_id": "lightup"}
        with self.assertRaisesRegex(ValueError, "unsupported lane_profile"):
            classify_backlog_item(project, item)


    def test_all_active_writers_block_conflicting_queued_other_lane(self):
        """A second active item in one lane must not disappear from admission."""
        active_first = self.item(
            "ftmo-active-1", "Implement strategy candidate", status="running",
            metadata={"conflict_scope": {"files": ["src/first.py"]}},
        )
        active_second = self.item(
            "ftmo-active-2", "Implement research recovery", status="verifying",
            metadata={"conflict_scope": {"files": ["src/shared.py"]}},
        )
        queued_qa = self.item(
            "ftmo-waiting", "Run frozen walk-forward validation",
            priority="P0",
            metadata={"conflict_scope": {"files": ["src/shared.py"]}},
        )
        lanes = generate_execution_lanes(
            self.project(), [queued_qa, active_second, active_first]
        )
        qa_lane = next(lane for lane in lanes if lane["lane_id"] == "qa-validation")
        self.assertEqual("blocked", qa_lane["status"])
        self.assertIsNone(qa_lane["queue_id"])
        self.assertEqual("ftmo-active-2", qa_lane["blocked_by"][0]["queue_id"])
        self.assertEqual(["src/shared.py"], qa_lane["blocked_by"][0]["overlap"]["files"])

    def test_single_scalar_claim_capability_blocks_real_lane(self):
        """Reject a conflicting claim supplied as one capability string."""
        project = self.project("cloud", "platform")
        candidate = {
            **self.item("cloud-runtime", "Repair Firefox automation"),
            "project_id": "cloud",
        }
        claims = [{
            "project_id": "cloud",
            "claim_key": "runner-control",
            "owner_id": "other",
            "metadata": {"conflict_scope": {
                "capabilities": "cloud:runtime-automation",
            }},
        }]
        lanes = generate_execution_lanes(project, [candidate], claims)
        runtime = next(lane for lane in lanes
                       if lane["lane_id"] == "runtime-automation")
        self.assertEqual("blocked", runtime["status"])
        self.assertIsNone(runtime["queue_id"])
        self.assertEqual("task_claim_scope_conflict",
                         runtime["blocked_by"][0]["reason"])

    def test_rejected_candidate_does_not_leave_blockers_on_safe_fallback(self):
        """The selected fallback is admitted and must report zero conflicts."""
        project = self.project("cloud", "platform")
        running = {
            **self.item("control-running", "Implement queue scheduler",
                        status="running",
                        metadata={"conflict_scope": {"files": ["shared.py"]}}),
            "project_id": "cloud",
        }
        rejected = {
            **self.item("runtime-rejected", "Repair Firefox automation",
                        priority="P0",
                        metadata={"conflict_scope": {"files": ["shared.py"]}}),
            "project_id": "cloud",
        }
        fallback = {
            **self.item("runtime-safe", "Repair Firefox tab state",
                        priority="P2",
                        metadata={"conflict_scope": {"files": ["browser/tab.py"]}}),
            "project_id": "cloud",
        }
        lanes = generate_execution_lanes(project, [fallback, rejected, running])
        runtime = next(x for x in lanes
                       if x["lane_id"] == "runtime-automation")
        self.assertEqual("queued", runtime["status"])
        self.assertEqual("runtime-safe", runtime["queue_id"])
        self.assertEqual([], runtime["blocked_by"])

    def test_malformed_active_scope_must_fail_before_admission(self):
        """Never silently discard a malformed active writer's file ownership."""
        project = self.project("cloud", "platform")
        active = {
            **self.item("control-active", "Implement queue scheduler",
                        status="running",
                        metadata={"conflict_scope": {
                            "files": {"path": "shared.py"},
                        }}),
            "project_id": "cloud",
        }
        queued = {
            **self.item("runtime-waiting", "Repair Firefox automation",
                        metadata={"conflict_scope": {
                            "files": ["shared.py"],
                        }}),
            "project_id": "cloud",
        }
        with self.assertRaisesRegex(ValueError, "invalid conflict_scope.files"):
            generate_execution_lanes(project, [queued, active])

    def test_malformed_task_claim_scope_must_fail_before_admission(self):
        """Unparseable claim entries cannot become an empty reserved scope."""
        project = self.project("cloud", "platform")
        queued = {
            **self.item("runtime-waiting", "Repair Firefox automation"),
            "project_id": "cloud",
        }
        claims = [{
            "project_id": "cloud",
            "claim_key": "already-owned",
            "metadata": {"conflict_scope": {"files": [None]}},
        }]
        with self.assertRaisesRegex(ValueError, "invalid conflict_scope.files"):
            generate_execution_lanes(project, [queued], claims)



if __name__ == "__main__":
    unittest.main()
