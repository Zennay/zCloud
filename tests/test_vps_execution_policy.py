import json
import unittest
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]


class VpsExecutionPolicyTests(unittest.TestCase):
    def test_policy_is_enabled_for_every_registered_project(self):
        policy = json.loads((ROOT / "vps-execution-policy.json").read_text(encoding="utf-8"))
        self.assertTrue(policy["enabled"])
        self.assertEqual(
            set(policy["projects"]),
            set(server.PROJECT_INDEX),
        )
        self.assertIn("github-actions-self-hosted-runner", policy["transport"])
        self.assertIn("vps-bb300bba", policy["runner"]["host"])
        self.assertIn("WAIT_VPS", policy["prompt_directive"])
        self.assertIn("verboden", policy["prompt_directive"])

    def test_server_has_no_implicit_vps_policy_fallback(self):
        self.assertFalse(hasattr(server, "_VPS_EXECUTION_POLICY_FALLBACK"))

    def test_every_project_prompt_contains_the_shared_execution_directive(self):
        for project_id, project in server.PROJECT_INDEX.items():
            prompt = server.project_runner_prompt(project_id, project["name"])
            self.assertIn("CENTRALE VPS/SSH-ROUTE", prompt)
            self.assertIn("self-hosted runner", prompt)
            self.assertIn("runner/workflow-id", prompt)

    def test_worker_prompt_keeps_the_route_when_assignment_is_added(self):
        prompt = server.project_worker_prompt(
            "zssh",
            server.PROJECT_INDEX["zssh"]["name"],
            server.project_runner_prompt("zssh", server.PROJECT_INDEX["zssh"]["name"]),
            1,
            1,
            {"queue_id": "probe", "project_id": "zssh", "priority": "P0", "title": "probe"},
        )
        self.assertIn("CENTRALE VPS/SSH-ROUTE", prompt)
        self.assertIn("VPS_QUEUE_ASSIGNMENT id=probe", prompt)


if __name__ == "__main__":
    unittest.main()
