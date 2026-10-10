"""Regression tests for sustained project-first worker prompts."""
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
    def test_prompt_is_project_first_and_sustained(self):
        prompt = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        self.assertTrue(prompt.startswith("Werk verder aan FTMO."))
        self.assertIn("Kijk in Notion in welke fase het project zit", prompt)
        self.assertIn("meerdere materiële stappen achter elkaar", prompt)
        self.assertIn("stop niet na één actie, commit, retrigger of statuscheck", prompt)
        self.assertIn("pak direct ander veilig uitvoerbaar werk", prompt)
        self.assertIn("tot de runlimiet", prompt)
        self.assertIn("Maak eerst bestaand open werk af", prompt)
        self.assertIn("geen nieuw issue of test-PR", prompt)
        self.assertLess(len(prompt), 1000)

    def test_parallel_worker_gets_compact_non_conflicting_lane_focus(self):
        prompt = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        self.assertIn("Parallel focus 1/2: data-provenance.", prompt)
        self.assertIn("niet-conflicterende werkgebied", prompt)
        self.assertIn("controleer open branches/PRs", prompt)
        self.assertNotIn("completion_criteria", prompt)
        self.assertNotIn("VPS_QUEUE_ASSIGNMENT", prompt)
        self.assertNotIn("ZCLOUD_QUEUE_", prompt)

    def test_parallel_lanes_produce_different_prompts_for_same_project(self):
        other = {
            **ITEM,
            "queue_id": "ftmo-qa",
            "execution_lane": {
                "lane_id": "qa-validation",
                "scope": {"capabilities": ["ftmo-qa"], "files": []},
            },
        }
        first = server.project_worker_prompt("ftmo", "FTMO", "", 1, 2, ITEM)
        second = server.project_worker_prompt("ftmo", "FTMO", "", 2, 2, other)
        self.assertNotEqual(first, second)
        self.assertIn("data-provenance", first)
        self.assertIn("qa-validation", second)

    def test_parallel_without_lane_still_avoids_duplicate_work(self):
        item = {"queue_id": "supa-q", "project_id": "supa"}
        prompt = server.project_worker_prompt("supa", "Supa", "", 2, 3, item)
        self.assertIn("Er werken 3 workers parallel", prompt)
        self.assertIn("ander vrij, niet-conflicterend werkgebied", prompt)
        self.assertIn("vermijd dubbel werk", prompt)

    def test_single_worker_uses_canonical_sustained_prompt(self):
        prompt = server.project_worker_prompt("supa", "Supa", "", 1, 1, None)
        self.assertEqual(server.project_runner_prompt("supa", "Supa"), prompt)

    def test_base_prompt_matches_owner_requested_shape(self):
        prompt = server.project_runner_prompt("zssh", "zSSH")
        self.assertTrue(prompt.startswith(
            "Werk verder aan zSSH. Kijk in Notion in welke fase het project zit, "
            "bepaal wat er nog gedaan moet worden en werk dat concreet uit."
        ))
        self.assertIn("Werk zelfstandig zo lang mogelijk hard door", prompt)
        self.assertIn("Kies steeds een vrij onderdeel", prompt)


if __name__ == "__main__":
    unittest.main()
