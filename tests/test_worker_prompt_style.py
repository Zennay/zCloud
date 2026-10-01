"""The worker prompt must make a worker keep going, not stop after one action (2026-10-01).

Zennay's own wording is the base; only the machine contract stays: the assignment line, the worker line
(both validated by the browser drivers) and the ZCLOUD_QUEUE_* block, which the clients parse to advance
the queue, now demanded only at the very end.
"""
import unittest

import server

ITEM = {"queue_id": "haxlab-gate", "project_id": "haxlab", "priority": "P2", "title": "Close the gate",
        "completion_criteria": "gate closed with evidence", "source_url": "https://example.invalid/s"}


def prompt_for(project_id="haxlab", item=ITEM, slot=2, total=3):
    return server.project_worker_prompt(project_id, server.PROJECT_INDEX[project_id]["name"], "", slot, total, item)


class WorkerPromptStyleTests(unittest.TestCase):
    def test_uses_zennays_own_simple_wording(self):
        prompt = prompt_for()
        for phrase in ("Ga verder met de VPS queue assignments uit de SQLite queue (source of truth)",
                       "Notion is alleen documentatie, nooit scheduler of blocker",
                       "Werk hier intens aan verder",
                       "connectoren met GitHub en Notion",
                       "zet je in een queue, dan pakt de zSSH-worker dat op"):
            self.assertIn(phrase, prompt)

    def test_tells_the_worker_to_keep_going_instead_of_one_action(self):
        prompt = prompt_for()
        self.assertIn("Stop niet na één actie", prompt)
        self.assertIn("Een status- of auditrapport is geen resultaat", prompt)

    def test_old_one_action_then_report_wording_is_gone(self):
        prompt = prompt_for()
        for stale in ("DOEN: voer vóór je antwoord minimaal één echte actie uit", "OUTPUT exact",
                      "CONTINUE alleen na zo'n actie", "Alleen lezen, auditen of status geven telt niet",
                      "Werk alleen aan VPS_QUEUE_ASSIGNMENT"):
            self.assertNotIn(stale, prompt)

    def test_result_block_is_demanded_only_at_the_very_end(self):
        prompt = prompt_for()
        marker = prompt.index("Pas helemaal aan het einde")
        self.assertLess(prompt.index("Stop niet na één actie"), marker)
        self.assertGreater(prompt.index("ZCLOUD_QUEUE_ITEM: haxlab-gate"), marker)
        self.assertGreater(prompt.index("ZCLOUD_QUEUE_RESULT: DONE|CONTINUE"), marker)
        self.assertTrue(prompt.rstrip().endswith("ZCLOUD_AUTONOMY: CONTINUE|WAIT_HUMAN|COMPLETE"))

    def test_machine_contract_and_guards_survive_for_every_project(self):
        for project_id, project in server.PROJECT_INDEX.items():
            item = dict(ITEM, queue_id=project_id + "-q", project_id=project_id)
            prompt = prompt_for(project_id, item, 1, 3)
            self.assertIn("VPS_QUEUE_ASSIGNMENT id=" + project_id + "-q", prompt)
            self.assertIn("Jij bent Worker 1/3.", prompt)
            self.assertIn("geen secrets in prompts/logs", prompt)  # from the shared VPS directive
            self.assertIn("Gebruik WAIT_HUMAN alleen voor een secret", prompt)
            self.assertLess(len(prompt), 1800, project_id)

    def test_project_specific_guards_still_present(self):
        self.assertIn("preregistration, walk-forward en final holdout", prompt_for("ftmo", dict(ITEM, queue_id="f", project_id="ftmo")))
        self.assertIn("ZCLOUD_FINAL_AUDIT: GREEN", prompt_for("cloud", dict(ITEM, queue_id="c", project_id="cloud")))

    def test_base_prompt_stays_short(self):
        for project_id, project in server.PROJECT_INDEX.items():
            self.assertLess(len(server.project_runner_prompt(project_id, project["name"])), 900, project_id)


if __name__ == "__main__":
    unittest.main()
