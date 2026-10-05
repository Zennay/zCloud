"""Regression tests for concise project-first worker prompts."""
import unittest

import server

ITEM = {
    "queue_id": "ftmo-gate",
    "project_id": "ftmo",
    "priority": "P0",
    "title": "Internal coordination item",
    "completion_criteria": "internal only",
    "source_url": "",
    "execution_lane": {
        "lane_id": "data-provenance",
        "scope": {"capabilities": ["ftmo-data-provenance"], "files": []},
    },
}


class WorkerPromptStyleTests(unittest.TestCase):
    def test_prompt_is_project_first_and_short(self):
        prompt = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        self.assertTrue(prompt.startswith("Werk verder aan FTMO."))
        self.assertIn("Kijk in Notion in welke fase het project zit", prompt)
        self.assertIn("wat er nog gedaan moet worden", prompt)
        self.assertIn("werk dat concreet uit", prompt)
        self.assertIn("research naar gebruikers", prompt)
        self.assertIn("vertaal bevindingen naar product, UX en prioriteiten", prompt)
        self.assertLess(len(prompt), 500)

    def test_internal_queue_details_are_not_exposed_in_prompt(self):
        prompt = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        for stale in (
            "VPS_QUEUE_ASSIGNMENT",
            "ZCLOUD_QUEUE_ITEM",
            "ZCLOUD_QUEUE_RESULT",
            "ZCLOUD_QUEUE_EVIDENCE",
            "ZCLOUD_NEXT_TASK",
            "Jij bent Worker",
            "Ga door met de queue",
            "SQLite queue",
            "DONE alleen met bewijs",
        ):
            self.assertNotIn(stale, prompt)

    def test_parallel_coordination_stays_out_of_the_visible_prompt(self):
        prompt = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        self.assertEqual(server.project_runner_prompt("ftmo", "FTMO"), prompt)
        self.assertNotIn("Werkgebied:", prompt)
        self.assertNotIn("claims", prompt)
        self.assertNotIn("worker", prompt.lower())

    def test_no_lane_uses_the_same_simple_prompt(self):
        prompt = server.project_worker_prompt("supa", "Supa", "", 1, 1, None)
        self.assertEqual(server.project_runner_prompt("supa", "Supa"), prompt)

    def test_base_prompt_matches_owner_requested_shape(self):
        prompt = server.project_runner_prompt("zssh", "zSSH")
        self.assertEqual(
            "Werk verder aan zSSH. Kijk in Notion in welke fase het project zit, "
            "bepaal wat er nog gedaan moet worden en werk dat concreet uit. "
            "Doe waar relevant eerst echte research naar gebruikers, feedback en markt/concurrenten "
            "en vertaal bevindingen naar product, UX en prioriteiten.",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
