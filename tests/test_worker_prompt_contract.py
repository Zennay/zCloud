"""Contract: browser drivers validate concise prompts using coordination metadata, not queue text."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import server
from tests._worker_env import WorkerEnv

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "worker_validators_harness.js"
PROJECTS = ("cloud", "haxlab", "raiseai", "ftmo", "supa", "ulab", "zssh")
SLOTS = ((1, 1), (1, 3), (2, 3), (3, 3))


def queue_item(project_id, slot):
    return {
        "queue_id": f"{project_id}-q",
        "project_id": project_id,
        "priority": "P1",
        "title": "Internal coordination",
        "completion_criteria": "internal only",
        "source_url": "",
        "worker_slot": slot,
    }


def make_case(project_id, slot, total, **override):
    item = queue_item(project_id, slot)
    cfg = {
        "active": True,
        "assignment_ready": True,
        "queue_item": item,
        "global_worker_slot": slot,
        "global_worker_count": total,
        "prompt": server.project_worker_prompt(
            project_id, server.PROJECT_INDEX[project_id]["name"], "", slot, total, item
        ),
    }
    cfg.update(override)
    return cfg


def run_validators(cases):
    result = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"cases": cases}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError("validator harness failed: " + result.stderr[-500:])
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node is required for the JS validator contract")
class WorkerPromptContractTests(unittest.TestCase):
    def test_server_prompt_satisfies_both_real_client_validators(self):
        cases = [make_case(p, s, t) for p in PROJECTS for s, t in SLOTS]
        out = run_validators(cases)
        for case, us, ex, diag in zip(cases, out["userscript"], out["extension"], out["diagnostic"]):
            label = f"{case['queue_item']['project_id']} {case['global_worker_slot']}/{case['global_worker_count']}"
            self.assertTrue(us, f"userscript refuses concise prompt for {label}: {diag}")
            self.assertTrue(ex, f"extension refuses concise prompt for {label}")
            self.assertEqual("", diag, label)
            self.assertEqual([], server.worker_contract_failures(case), label)

    def test_missing_project_instruction_is_rejected_and_named(self):
        case = make_case("cloud", 1, 3)
        case["prompt"] = case["prompt"].replace("Werk verder aan ", "Ga door met ")
        out = run_validators([case])
        self.assertFalse(out["userscript"][0])
        self.assertFalse(out["extension"][0])
        self.assertIn("prompt-missing-project-instruction", out["diagnostic"][0])
        self.assertIn("prompt-missing-project-instruction", server.worker_contract_failures(case))

    def test_missing_notion_phase_instruction_is_rejected_and_named(self):
        case = make_case("cloud", 1, 3)
        case["prompt"] = case["prompt"].replace("Kijk in Notion in welke fase het project zit", "Bekijk het project")
        out = run_validators([case])
        self.assertFalse(out["userscript"][0])
        self.assertFalse(out["extension"][0])
        self.assertIn("prompt-missing-notion-phase", out["diagnostic"][0])
        self.assertIn("prompt-missing-notion-phase", server.worker_contract_failures(case))

    def test_python_mirror_and_js_diagnostic_agree_on_metadata_failures(self):
        base = make_case("haxlab", 2, 3)
        cases = [
            base,
            make_case("haxlab", 2, 3, active=False),
            make_case("haxlab", 2, 3, assignment_ready=False),
            make_case("haxlab", 2, 3, queue_item=None),
            make_case("haxlab", 2, 3, global_worker_slot=0),
            make_case("haxlab", 2, 3, global_worker_count=1),
            make_case("haxlab", 2, 3, prompt=""),
        ]
        out = run_validators(cases)
        for index, case in enumerate(cases):
            py = server.worker_contract_failures(case)
            js = [p for p in out["diagnostic"][index].split(",") if p]
            self.assertEqual(py, js, f"case {index}: python mirror and JS diagnostic disagree")
            self.assertEqual(not py, out["userscript"][index], f"case {index}: userscript validator vs mirror")
            py_ext = [p for p in py if p != "not-active"]
            self.assertEqual(not py_ext, out["extension"][index], f"case {index}: extension validator vs mirror")

    def test_live_runner_worker_targets_pass_both_validators(self):
        with WorkerEnv():
            workers = [w for w in server.runner_worker_targets().values() if w["active"]]
            self.assertGreaterEqual(len(workers), 3)
            out = run_validators(workers)
            for worker, us, ex in zip(workers, out["userscript"], out["extension"]):
                self.assertTrue(us, f"{worker['project_id']}: userscript refuses real runner-targets payload")
                self.assertTrue(ex, f"{worker['project_id']}: extension refuses real runner-targets payload")
                self.assertEqual([], server.worker_contract_failures(worker), worker["project_id"])

    def test_stale_global_slot_mapping_never_binds_another_projects_queue_item(self):
        with WorkerEnv():
            allocation = server.global_worker_allocation()["workers"]
            by_slot = {int(item["global_worker_slot"]): item for item in allocation}
            slot_two = by_slot[2]
            slot_three = by_slot[3]
            self.assertNotEqual(slot_two["project_id"], slot_three["project_id"])

            # Simulate the one-tick race observed live: the persisted browser-slot map
            # still points slot 2 at the project that the queue has already moved to
            # slot 3. The worker payload must fail closed instead of inheriting the
            # current slot-2 project's queue item.
            stale_project = slot_three["project_id"]
            with server.connect() as conn:
                conn.execute("DELETE FROM ai_global_slots WHERE slot=?", (3,))
                conn.execute(
                    "UPDATE ai_global_slots SET project_id=?,worker_slot=? WHERE slot=?",
                    (stale_project, 1, 2),
                )

            worker = server.runner_worker_targets()[f"{stale_project}::w1"]
            self.assertTrue(worker["active"])
            self.assertEqual(2, worker["global_worker_slot"])
            self.assertIsNone(worker["queue_item"])
            self.assertFalse(worker["assignment_ready"])
            self.assertIn("server-assignment-not-ready", server.worker_contract_failures(worker))
            self.assertIn("missing-queue-id", server.worker_contract_failures(worker))

    def test_bounded_senior_reviewer_is_valid_without_execution_queue_slot(self):
        with WorkerEnv():
            with server.connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO runner_targets(project_id,name,conversation_id,prompt,active,worker_count) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        "portfolio-review",
                        "Portfolio Birdseye Review",
                        "",
                        "Je bent de Portfolio Bird's-eye Reviewer. Gebruik de Senior Team OS-regel Periodic Strategy & Architecture Challenge.",
                        1,
                        1,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)",
                    ("portfolio-review", 1, ""),
                )

            reviewer = server.runner_worker_targets()["portfolio-review::w1"]
            self.assertTrue(reviewer["reviewer_mode"])
            self.assertTrue(reviewer["active"])
            self.assertTrue(reviewer["assignment_ready"])
            self.assertIsNone(reviewer["queue_item"])
            self.assertIsNone(reviewer["global_worker_slot"])
            self.assertNotIn("portfolio-review", server.global_worker_allocation()["projects"])

            out = run_validators([reviewer])
            self.assertTrue(out["userscript"][0], out["diagnostic"][0])
            self.assertTrue(out["extension"][0])
            self.assertEqual([], server.worker_contract_failures(reviewer))

            impostor = dict(reviewer, base_project_id="ftmo")
            bad = run_validators([impostor])
            self.assertFalse(bad["userscript"][0])
            self.assertFalse(bad["extension"][0])
            self.assertIn("reviewer-wrong-project", bad["diagnostic"][0])
            self.assertIn("reviewer-wrong-project", server.worker_contract_failures(impostor))


if __name__ == "__main__":
    unittest.main()
