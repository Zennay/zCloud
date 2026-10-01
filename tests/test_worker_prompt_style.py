"""The worker prompt must make a worker keep going, not stop after one action (2026-10-01).

Zennay's own wording is the base; the machine contract stays: the assignment line and the worker line
(both validated by the browser drivers) and the ZCLOUD_QUEUE_* block, which the clients parse to advance
the queue, demanded only at the very end. At the end the worker also thinks zCloud-wide about what the
queue still needs, reported as ONE ZCLOUD_NEXT_TASK line (parsed by both clients, enqueued by
portfolio_queue_finish).
"""
import re
import unittest

import server

ITEM = {"queue_id": "haxlab-gate", "project_id": "haxlab", "priority": "P2", "title": "Close the gate",
        "completion_criteria": "gate closed with evidence", "source_url": "https://example.invalid/s"}


def prompt_for(project_id="haxlab", item=ITEM, slot=2, total=3):
    return server.project_worker_prompt(project_id, server.PROJECT_INDEX[project_id]["name"], "", slot, total, item)


class WorkerPromptStyleTests(unittest.TestCase):
    def test_uses_zennays_own_simple_wording(self):
        prompt = prompt_for()
        for phrase in ("Ga door met de queue",
                       "Notion is alleen documentatie, nooit scheduler of blocker",
                       "Werk hier intens aan verder",
                       "pakt dat op"):
            self.assertIn(phrase, prompt)

    def test_says_where_the_queue_is(self):
        prompt = prompt_for()
        self.assertIn("de SQLite queue van zCloud op de VPS (portfolio_queue, source of truth)", prompt)
        self.assertIn("jouw assignment staat hieronder", prompt)
        self.assertLess(prompt.index("Ga door met de queue"), prompt.index("VPS_QUEUE_ASSIGNMENT id=haxlab-gate"))

    def test_names_the_connectors(self):
        prompt = prompt_for()
        self.assertIn("je connectoren: GitHub (code, PR, Actions) en Notion (HQ, handoff)", prompt)

    def test_ssh_work_goes_into_the_queue_for_the_zssh_worker(self):
        self.assertIn("zet je in de queue voor project zssh; de zSSH-worker pakt dat op", prompt_for())

    def test_tells_the_worker_to_keep_going_instead_of_one_action(self):
        prompt = prompt_for()
        self.assertIn("Stop niet na één actie", prompt)
        self.assertIn("Een status- of auditrapport is geen resultaat", prompt)

    def test_old_one_action_then_report_wording_is_gone(self):
        prompt = prompt_for()
        for stale in ("DOEN: voer vóór je antwoord minimaal één echte actie uit", "OUTPUT exact",
                      "CONTINUE alleen na zo'n actie", "Alleen lezen, auditen of status geven telt niet",
                      "Werk alleen aan VPS_QUEUE_ASSIGNMENT", "Ga verder met de VPS queue assignments"):
            self.assertNotIn(stale, prompt)

    def test_result_block_is_demanded_only_at_the_very_end(self):
        prompt = prompt_for()
        marker = prompt.index("Pas helemaal aan het einde")
        self.assertLess(prompt.index("Stop niet na één actie"), marker)
        self.assertGreater(prompt.index("ZCLOUD_QUEUE_ITEM: haxlab-gate"), marker)
        self.assertGreater(prompt.index("ZCLOUD_QUEUE_RESULT: DONE|CONTINUE"), marker)
        self.assertTrue(prompt.rstrip().endswith("ZCLOUD_AUTONOMY: CONTINUE|WAIT_HUMAN|COMPLETE"))

    def test_at_the_end_the_worker_thinks_zcloud_wide_about_the_queue(self):
        prompt = prompt_for()
        marker = prompt.index("Pas helemaal aan het einde")
        self.assertIn("Bij DONE of BLOCKED denk je eerst breder over heel zCloud na", prompt[marker:])
        self.assertIn("wat moet er nog aan de queue komen?", prompt[marker:])
        self.assertIn("precies één ZCLOUD_NEXT_TASK", prompt[marker:])
        self.assertIn("Bij CONTINUE laat je die regel weg", prompt[marker:])

    def test_next_task_template_lists_every_valid_project_and_is_parsed_like_the_clients_do(self):
        prompt = prompt_for()
        line = re.search(r"^ZCLOUD_NEXT_TASK:\s*(.+)$", prompt, re.M).group(1)
        self.assertIn("project=<" + "|".join(sorted(server.PROJECT_INDEX)) + ">", line)
        # fill the template the way a worker would, then parse it exactly like nextTaskFromText() does
        filled = "project=zssh; priority=P1; title=Deploy the release; criteria=green workflow run"
        parts = dict((k.strip().lower(), v.strip()) for k, v in (p.split("=", 1) for p in filled.split(";")))
        self.assertEqual({"project": "zssh", "priority": "P1", "title": "Deploy the release",
                          "criteria": "green workflow run"}, parts)
        self.assertIn(parts["project"], server.PROJECT_INDEX)

    def test_a_worker_next_task_really_lands_in_the_queue_as_a_child(self):
        from tests._worker_env import WorkerEnv
        with WorkerEnv():
            item = server.portfolio_queue_items()[0]
            server.portfolio_queue_allocate()
            claimed = [i for i in server.portfolio_queue_items() if i.get("worker_slot")][0]
            result = server.portfolio_queue_finish(
                claimed["worker_slot"], claimed["queue_id"], "DONE", "evidence",
                {"project_id": "zssh", "priority": "P1", "title": "Release via SSH", "completion_criteria": "green run"})
            self.assertTrue(result["updated"])
            created = result["next_task"]
            self.assertEqual("zssh", created["project_id"])
            self.assertEqual(claimed["queue_id"], created["parent_queue_id"])
            self.assertEqual("queued", created["status"])
            self.assertIsNotNone(item)

    def test_machine_contract_and_guards_survive_for_every_project(self):
        for project_id, project in server.PROJECT_INDEX.items():
            item = dict(ITEM, queue_id=project_id + "-q", project_id=project_id)
            prompt = prompt_for(project_id, item, 1, 3)
            self.assertIn("VPS_QUEUE_ASSIGNMENT id=" + project_id + "-q", prompt)
            self.assertIn("Jij bent Worker 1/3.", prompt)
            self.assertIn("geen secrets in prompts/logs", prompt)  # from the shared VPS directive
            self.assertIn("Gebruik WAIT_HUMAN alleen voor een secret", prompt)
            self.assertLess(len(prompt), 2500, project_id)

    def test_project_specific_guards_still_present(self):
        self.assertIn("preregistration, walk-forward en final holdout", prompt_for("ftmo", dict(ITEM, queue_id="f", project_id="ftmo")))
        self.assertIn("ZCLOUD_FINAL_AUDIT: GREEN", prompt_for("cloud", dict(ITEM, queue_id="c", project_id="cloud")))

    def test_base_prompt_stays_short(self):
        for project_id, project in server.PROJECT_INDEX.items():
            self.assertLess(len(server.project_runner_prompt(project_id, project["name"])), 900, project_id)


if __name__ == "__main__":
    unittest.main()
