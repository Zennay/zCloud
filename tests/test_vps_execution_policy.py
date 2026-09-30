import json
import unittest
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]


class VpsExecutionPolicyTests(unittest.TestCase):
    def test_policy_is_enabled_for_every_registered_project(self):
        policy = json.loads((ROOT / "vps-execution-policy.json").read_text(encoding="utf-8"))
        self.assertTrue(policy["enabled"])
        self.assertEqual(set(policy["projects"]), set(server.PROJECT_INDEX))
        self.assertIn("github-actions-self-hosted-runner", policy["transport"])
        self.assertIn("vps-bb300bba", policy["runner"]["host"])
        self.assertIn("WAIT_VPS", policy["prompt_directive"])
        self.assertIn("niet gebruiken", policy["prompt_directive"])

    def test_server_has_no_implicit_vps_policy_fallback(self):
        self.assertFalse(hasattr(server, "_VPS_EXECUTION_POLICY_FALLBACK"))

    def test_every_project_prompt_contains_the_short_shared_execution_directive(self):
        for project_id, project in server.PROJECT_INDEX.items():
            prompt = server.project_runner_prompt(project_id, project["name"])
            self.assertIn("self-hosted vps-bb300bba", prompt)
            self.assertIn("workflow_run_id", prompt)
            self.assertLess(len(prompt), 900)

    def test_worker_prompt_keeps_the_route_when_assignment_is_added(self):
        prompt = server.project_worker_prompt(
            "zssh",
            server.PROJECT_INDEX["zssh"]["name"],
            "old persisted prompt that must be ignored",
            1,
            1,
            {"queue_id": "probe", "project_id": "zssh", "priority": "P0", "title": "probe"},
        )
        self.assertIn("self-hosted vps-bb300bba", prompt)
        self.assertIn("VPS_QUEUE_ASSIGNMENT id=probe", prompt)
        self.assertNotIn("old persisted prompt", prompt)


if __name__ == "__main__":
    unittest.main()
